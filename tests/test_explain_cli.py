"""Unit/CLI tests for governance explain."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import governance
from governance.cli import main
from governance.domain.authority import (
    AuthorityDeclaration,
    AuthorityRuleKey,
    AuthoritySelector,
    AuthorityTarget,
    NormalizedAuthorityPolicySet,
    NormalizedAuthorityRule,
)
from governance.domain.graph import (
    NODE_KIND_TABLE,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationSet,
    PropertyPath,
)
from governance.reconciliation.explain import (
    build_explain_result,
    format_explain_human,
)
from governance.reconciliation.sources import ReconciliationSourceBundle

NS = "acme.commerce"
PATH_DESC = PropertyPath(("description",))
CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _identity(logical_id: str = "orders") -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, NODE_KIND_TABLE, logical_id)


def _prov(provider: str, ref: str, version: str | None = "1.0") -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type=provider,
        source_ref=ref,
        source_version=version,
        observation_mode="declared",
    )


def _obs(
    value: object,
    *provenances: ProvenanceRecord,
    identity: GraphNodeIdentity | None = None,
) -> PropertyObservation:
    return PropertyObservation(
        object_identity=identity or _identity(),
        property_path=PATH_DESC,
        value=value,
        provenance=provenances,
    )


def _bundle(*observations: PropertyObservation) -> ReconciliationSourceBundle:
    obs_set = PropertyObservationSet.from_observations(observations)
    known_map = {
        item.object_identity.canonical_bytes(): item.object_identity
        for item in observations
    }
    known = tuple(known_map.values())
    return ReconciliationSourceBundle(observations=obs_set, known_objects=known)


def _rule(provider: str) -> NormalizedAuthorityRule:
    return NormalizedAuthorityRule(
        key=AuthorityRuleKey(
            selector=AuthoritySelector(kind=NODE_KIND_TABLE, property_path=PATH_DESC),
            authority=AuthorityTarget(provider_type=provider),
        ),
        declarations=(AuthorityDeclaration("rule"),),
    )


def _write_explain_workspace(tmp_path: Path) -> Path:
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / "governance.yaml"
    config.write_text(
        "\n".join(
            [
                'schema_version: "1"',
                "sources:",
                "  - id: primary",
                "    provider: postgresql",
                "    config:",
                "      source_name: governance-demo",
                "      connection:",
                "        database_url_env: DATABASE_URL",
                "targets:",
                "  - id: collibra",
                "    provider: collibra",
                "    config:",
                "      mode_env: COLLIBRA_MODE",
                "      mapping:",
                "        path: mapping.json",
                "policies:",
                "  files: []",
                "authority:",
                "  files: []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _minimal_odcs(*, description: str = "orders purpose") -> dict[str, Any]:
    return {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
        "description": {"purpose": description},
        "schema": [
            {
                "name": "orders",
                "physicalType": "table",
                "description": description,
                "properties": [
                    {
                        "name": "order_id",
                        "logicalType": "string",
                        "physicalType": "varchar",
                        "description": "Order identifier",
                    }
                ],
            }
        ],
    }


def _dataset_identity_from_odcs() -> GraphNodeIdentity:
    # ODCS datasets are root identities (no contract parent).
    return GraphNodeIdentity(NS, "dataset", "orders", parent=None)


def test_package_version_1_3_0() -> None:
    assert governance.__version__ == "1.3.0"


def test_explain_json_single_agreement_resolved_unresolved() -> None:
    identity = _identity()

    single = build_explain_result(
        namespace=NS,
        identity=identity,
        bundle=_bundle(_obs("only", _prov("odcs", "c1"))),
        authority=NormalizedAuthorityPolicySet(),
        property_filter=PATH_DESC,
    )
    assert single["properties"][0]["state"] == "SINGLE_OBSERVATION"
    assert single["writes_performed"] == 0
    assert single["explain_schema"] == "governance-explain-result"

    agreement = build_explain_result(
        namespace=NS,
        identity=identity,
        bundle=_bundle(_obs("shared", _prov("odcs", "c1"), _prov("dbt", "m"))),
        authority=NormalizedAuthorityPolicySet(),
        property_filter=PATH_DESC,
    )
    assert agreement["properties"][0]["state"] == "AGREEMENT"

    resolved = build_explain_result(
        namespace=NS,
        identity=identity,
        bundle=_bundle(
            _obs("from-odcs", _prov("odcs", "c1")),
            _obs("from-dbt", _prov("dbt", "m")),
        ),
        authority=NormalizedAuthorityPolicySet(rules=(_rule("odcs"),)),
        property_filter=PATH_DESC,
    )
    assert resolved["properties"][0]["state"] == "RESOLVED_BY_AUTHORITY"
    assert resolved["properties"][0]["effective_value"] == "from-odcs"
    assert resolved["properties"][0]["winning_rule_key"] is not None

    unresolved = build_explain_result(
        namespace=NS,
        identity=identity,
        bundle=_bundle(
            _obs("a", _prov("odcs", "c1")),
            _obs("b", _prov("dbt", "m")),
        ),
        authority=NormalizedAuthorityPolicySet(),
        property_filter=PATH_DESC,
    )
    assert unresolved["properties"][0]["state"] == "UNRESOLVED_CONFLICT"
    assert unresolved["properties"][0]["has_effective_value"] is False


def test_unknown_object_and_property_diagnostics_exit_4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("COLLIBRA_MODE", "mock")
    config = _write_explain_workspace(tmp_path)
    odcs_path = tmp_path / "contract.json"
    odcs_path.write_text(json.dumps(_minimal_odcs()), encoding="utf-8")

    unknown_obj = tmp_path / "unknown-object.json"
    unknown_obj.write_text(
        json.dumps(
            {
                "namespace": NS,
                "kind": "table",
                "logical_id": "missing",
                "parent": None,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    code = main(
        [
            "explain",
            "--config",
            str(config),
            "--namespace",
            NS,
            "--object-identity",
            str(unknown_obj),
            "--odcs",
            str(odcs_path),
            "--format",
            "json",
        ]
    )
    assert code == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostic_schema"] == "governance-explain-diagnostics"
    assert payload["errors"][0]["code"] == "unknown_object"

    dataset = _dataset_identity_from_odcs()
    known_obj = tmp_path / "known-object.json"
    known_obj.write_text(json.dumps(dataset.to_dict(), indent=2) + "\n", encoding="utf-8")
    code = main(
        [
            "explain",
            "--config",
            str(config),
            "--namespace",
            NS,
            "--object-identity",
            str(known_obj),
            "--property",
            "/attributes/not_observed",
            "--odcs",
            str(odcs_path),
            "--format",
            "json",
        ]
    )
    assert code == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "unknown_property"


def test_human_output_contains_required_sections() -> None:
    result = build_explain_result(
        namespace=NS,
        identity=_identity(),
        bundle=_bundle(
            _obs("from-odcs", _prov("odcs", "c1")),
            _obs("from-dbt", _prov("dbt", "m")),
        ),
        authority=NormalizedAuthorityPolicySet(rules=(_rule("odcs"),)),
        property_filter=PATH_DESC,
    )
    human = format_explain_human(result)
    assert "OBJECT " in human
    assert "PROPERTY " in human
    assert "VALUE_GROUP " in human
    assert "PROVENANCE " in human
    assert "writes=0" in human
    assert "AUTHORITY " in human


def test_no_source_paths_in_explain_json() -> None:
    result = build_explain_result(
        namespace=NS,
        identity=_identity(),
        bundle=_bundle(_obs("only", _prov("odcs", "c1"))),
        authority=NormalizedAuthorityPolicySet(),
    )
    encoded = json.dumps(result, sort_keys=True)
    assert "C:\\\\" not in encoded
    assert "/home/" not in encoded
    assert "/Users/" not in encoded
    assert "contract.json" not in encoded
    assert "manifest.json" not in encoded


def test_content_identity_stable_under_path_reorder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("COLLIBRA_MODE", "mock")
    config = _write_explain_workspace(tmp_path)

    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    body = json.dumps(_minimal_odcs())
    (left / "a.json").write_text(body, encoding="utf-8")
    (left / "b.json").write_text(body, encoding="utf-8")
    shutil.copy(left / "a.json", right / "x.json")
    shutil.copy(left / "b.json", right / "y.json")

    dataset = _dataset_identity_from_odcs()
    obj = tmp_path / "object.json"
    obj.write_text(json.dumps(dataset.to_dict(), indent=2) + "\n", encoding="utf-8")

    def run(first: Path, second: Path) -> dict[str, Any]:
        code = main(
            [
                "explain",
                "--config",
                str(config),
                "--namespace",
                NS,
                "--object-identity",
                str(obj),
                "--odcs",
                str(first),
                "--odcs",
                str(second),
                "--format",
                "json",
            ]
        )
        assert code == 0
        return json.loads(capsys.readouterr().out)

    first = run(left / "a.json", left / "b.json")
    second = run(right / "y.json", right / "x.json")
    assert first["content_identity"] == second["content_identity"]
    assert str(left) not in json.dumps(first)
    assert str(right) not in json.dumps(second)

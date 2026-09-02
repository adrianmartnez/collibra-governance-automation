"""Property observations artifact persist/load tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest_history import build_observation_set, write_observations
from governance.identity.hashing import property_observation_set_identity
from governance.observations import (
    PROPERTY_OBSERVATION_SET_SCHEMA,
    ObservationsArtifactError,
    load_property_observation_set_artifact,
    property_observation_set_to_json,
    write_property_observation_set,
)


def _assert_code(exc: ObservationsArtifactError, code: str) -> None:
    assert any(item.code == code for item in exc.errors)


def _inject_duplicate_key(text: str, key: str, earlier_json_value: str) -> str:
    needle = f'"{key}":'
    index = text.index(needle)
    return text[:index] + f'  "{key}": {earlier_json_value},\n' + text[index:]


def _rewrite_with_valid_identity(path: Path, mutator: Callable[[dict[str, Any]], None]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutator(payload)
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    # Rebuild identity from mutated payload observations via identity helper on body.
    # For semantic rejects we still need a structurally plausible identity block.
    from governance.observations.artifact import PROPERTY_OBSERVATION_SET_SCHEMA as _schema

    assert without.get("observation_schema") == _schema
    # Keep existing digest shape; integrity may fail before semantic in some cases.
    # Prefer recomputing from parsed set when mutation preserves loadable shape.
    try:
        # Use the stored observations list fingerprint path when possible.
        identity_body = {
            "observation_schema": without["observation_schema"],
            "observation_version": without["observation_version"],
            "observations": without["observations"],
        }
        payload["content_identity"] = property_observation_set_identity(identity_body).to_dict()
    except Exception:
        payload["content_identity"] = {
            "algorithm": "sha256",
            "hashing_contract_version": "1",
            "digest": "0" * 64,
        }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_observations_round_trip(tmp_path: Path) -> None:
    observation_set = build_observation_set(value={"token": "business-value"})
    path = tmp_path / "obs.json"
    write_property_observation_set(observation_set, path)
    loaded = load_property_observation_set_artifact(path)
    assert loaded.content_identity() == observation_set.content_identity()
    assert path.read_text(encoding="utf-8") == property_observation_set_to_json(observation_set)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["observation_schema"] == PROPERTY_OBSERVATION_SET_SCHEMA
    assert payload["observations"][0]["value"] == {"token": "business-value"}


def test_opaque_token_in_value_ok(tmp_path: Path) -> None:
    write_observations(tmp_path / "obs.json", build_observation_set(value={"token": "x"}))
    loaded = load_property_observation_set_artifact(tmp_path / "obs.json")
    assert loaded.observations[0].value == {"token": "x"}


def test_envelope_token_rejected(tmp_path: Path) -> None:
    write_observations(tmp_path / "obs.json")
    payload = json.loads((tmp_path / "obs.json").read_text(encoding="utf-8"))
    payload["token"] = "leak"
    (tmp_path / "obs.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(tmp_path / "obs.json")
    assert any(err.code == "invalid_artifact" for err in exc_info.value.errors)


def test_integrity_mismatch(tmp_path: Path) -> None:
    write_observations(tmp_path / "obs.json")
    payload = json.loads((tmp_path / "obs.json").read_text(encoding="utf-8"))
    payload["content_identity"]["digest"] = "0" * 64
    (tmp_path / "obs.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(tmp_path / "obs.json")
    assert any(err.code == "integrity_mismatch" for err in exc_info.value.errors)


def test_unsupported_schema(tmp_path: Path) -> None:
    write_observations(tmp_path / "obs.json")
    payload = json.loads((tmp_path / "obs.json").read_text(encoding="utf-8"))
    payload["observation_schema"] = "other"
    (tmp_path / "obs.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(tmp_path / "obs.json")
    assert any(err.code == "unsupported_schema" for err in exc_info.value.errors)


def test_invalid_utf8_bytes_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    path.write_bytes(b'{"observation_schema": "\xff"}')
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "parse_error")


def test_duplicate_root_json_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    write_observations(path)
    text = path.read_text(encoding="utf-8")
    path.write_text(_inject_duplicate_key(text, "observation_schema", '"other"'), encoding="utf-8")
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "parse_error")


def test_duplicate_nested_json_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    write_observations(path)
    text = path.read_text(encoding="utf-8")
    path.write_text(_inject_duplicate_key(text, "algorithm", '"md5"'), encoding="utf-8")
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "parse_error")


@pytest.mark.parametrize(
    ("literal",),
    [
        ("NaN",),
        ("Infinity",),
        ("-Infinity",),
        ("1e999",),
    ],
)
def test_non_finite_json_literals_rejected(tmp_path: Path, literal: str) -> None:
    path = tmp_path / "obs.json"
    path.write_text(f'{{"value": {literal}}}\n', encoding="utf-8")
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "parse_error")


def test_recursion_error_in_json_loads_maps_to_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_observations(tmp_path / "obs.json")

    def _boom(*args, **kwargs):
        raise RecursionError("too deep")

    monkeypatch.setattr("governance.observations.artifact.json.loads", _boom)
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(tmp_path / "obs.json")
    _assert_code(exc_info.value, "parse_error")


def test_recursion_error_in_validate_json_value_maps_to_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_observations(tmp_path / "obs.json")
    real_loads = json.loads

    def _loads_with_validate_boom(*args, **kwargs):
        payload = real_loads(*args, **kwargs)
        monkeypatch.setattr(
            "governance.observations.artifact.validate_json_value",
            lambda _value: (_ for _ in ()).throw(RecursionError("too deep")),
        )
        return payload

    monkeypatch.setattr("governance.observations.artifact.json.loads", _loads_with_validate_boom)
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(tmp_path / "obs.json")
    _assert_code(exc_info.value, "parse_error")


def test_recursion_error_in_schema_iter_errors_maps_to_invalid_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_observations(tmp_path / "obs.json")

    class _BoomValidator:
        def iter_errors(self, _payload):
            raise RecursionError("too deep")

    monkeypatch.setattr(
        "governance.observations.artifact.Draft202012Validator",
        lambda _schema: _BoomValidator(),
    )
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(tmp_path / "obs.json")
    _assert_code(exc_info.value, "invalid_artifact")
    assert any("too deeply nested" in item.message for item in exc_info.value.errors)


def test_real_depth_parent_chain_schema_smoke(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    write_observations(path)
    parent: dict[str, Any] | None = None
    for index in range(60):
        parent = {
            "namespace": "acme.commerce",
            "kind": "table",
            "logical_id": f"parent-{index}",
            "parent": parent,
        }

    def mutate(payload: dict[str, Any]) -> None:
        payload["observations"][0]["object"]["parent"] = parent

    _rewrite_with_valid_identity(path, mutate)
    try:
        load_property_observation_set_artifact(path)
    except ObservationsArtifactError as exc:
        assert any(
            item.code == "invalid_artifact"
            and ("too deeply nested" in item.message or "schema validation" in item.message)
            for item in exc.errors
        )


def test_malformed_recursive_graph_node_identity_rejected(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    write_observations(path)

    def mutate(payload: dict[str, Any]) -> None:
        payload["observations"][0]["object"]["parent"] = {
            "namespace": "acme.commerce",
            "kind": "table",
            "logical_id": "orders",
            # missing parent key -> exact-key reject
        }

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "invalid_artifact")


def test_numeric_coerced_identity_fields_rejected(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    write_observations(path)

    def mutate(payload: dict[str, Any]) -> None:
        payload["observations"][0]["object"]["logical_id"] = 123

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "invalid_artifact")


def test_invalid_property_path_rejected(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    write_observations(path)

    def mutate(payload: dict[str, Any]) -> None:
        payload["observations"][0]["property"] = "description"

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "invalid_artifact")


def test_non_canonical_property_path_rejected(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    write_observations(path)

    def mutate(payload: dict[str, Any]) -> None:
        # Invalid escape is rejected; also covers non-canonical pointer gate.
        payload["observations"][0]["property"] = "/description/~"

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "invalid_artifact")


def test_malformed_provenance_record_rejected(tmp_path: Path) -> None:
    path = tmp_path / "obs.json"
    write_observations(path)

    def mutate(payload: dict[str, Any]) -> None:
        payload["observations"][0]["provenance"][0]["provider_type"] = 1

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    _assert_code(exc_info.value, "invalid_artifact")


def test_non_normalized_observation_order_rejected(tmp_path: Path) -> None:
    from governance.domain.graph import GraphNodeIdentity, ProvenanceRecord
    from governance.domain.observations import (
        PropertyObservation,
        PropertyObservationSet,
        PropertyPath,
    )

    identity_a = GraphNodeIdentity("acme.commerce", "table", "orders")
    identity_b = GraphNodeIdentity("acme.commerce", "table", "customers")
    path_prop = PropertyPath.parse("/description")
    obs_a = PropertyObservation(
        object_identity=identity_a,
        property_path=path_prop,
        value="orders",
        provenance=(ProvenanceRecord("odcs", "c1", "1.0", "observed"),),
    )
    obs_b = PropertyObservation(
        object_identity=identity_b,
        property_path=path_prop,
        value="customers",
        provenance=(ProvenanceRecord("odcs", "c1", "1.0", "observed"),),
    )
    # Domain normalizes to customers before orders lexicographically by identity.
    normalized = PropertyObservationSet.from_observations([obs_a, obs_b])
    path = tmp_path / "obs.json"
    write_property_observation_set(normalized, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Reverse order relative to normalized storage
    payload["observations"] = list(reversed(payload["observations"]))
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    identity_body = {
        "observation_schema": without["observation_schema"],
        "observation_version": without["observation_version"],
        "observations": without["observations"],
    }
    payload["content_identity"] = property_observation_set_identity(identity_body).to_dict()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ObservationsArtifactError) as exc_info:
        load_property_observation_set_artifact(path)
    assert any("not in domain-normalized order" in item.message for item in exc_info.value.errors)


def test_deterministic_roundtrip_bytes(tmp_path: Path) -> None:
    observation_set = build_observation_set(value={"token": "business-value"})
    path = tmp_path / "obs.json"
    write_property_observation_set(observation_set, path)
    first = path.read_bytes()
    loaded = load_property_observation_set_artifact(path)
    write_property_observation_set(loaded, path)
    assert path.read_bytes() == first
    assert first.decode("utf-8") == property_observation_set_to_json(observation_set)

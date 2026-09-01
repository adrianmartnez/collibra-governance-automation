"""CLI pre-I/O gates: invalid authority must not trigger operational I/O."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from governance.cli import main

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _write_invalid_authority_config(tmp_path: Path) -> Path:
    """Config that passes config contract but fails authority side-file load."""
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
                "      auth:",
                "        base_url_env: COLLIBRA_BASE_URL",
                "authority:",
                "  files:",
                "    - authority/missing.yaml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5432/governance_demo",
    )
    monkeypatch.setenv("COLLIBRA_MODE", "mock")
    monkeypatch.setenv("COLLIBRA_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("COLLIBRA_BEARER_TOKEN", "secret-bearer-token")


def _spy_scan(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"scan": 0}

    def boom(_settings: object) -> object:
        calls["scan"] += 1
        raise AssertionError("scanner must not run when authority is invalid")

    monkeypatch.setattr("governance.cli._scan_model", boom)
    return calls


def _spy_collibra(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"adapter": 0, "preflight": 0}

    def boom_adapter(*_a: object, **_k: object) -> object:
        calls["adapter"] += 1
        raise AssertionError("Collibra adapter must not run when authority is invalid")

    def boom_preflight(*_a: object, **_k: object) -> object:
        calls["preflight"] += 1
        raise AssertionError("preflight must not run when authority is invalid")

    monkeypatch.setattr("governance.cli.build_collibra_adapter", boom_adapter)
    monkeypatch.setattr("governance.cli.run_preflight", boom_preflight)
    return calls


@pytest.mark.parametrize(
    ("argv_builder",),
    [
        (lambda config, tmp: ["scan", "--config", str(config)],),
        (
            lambda config, tmp: [
                "export",
                "--config",
                str(config),
                "--output",
                str(tmp / "out.json"),
            ],
        ),
        (lambda config, tmp: ["diff", "--config", str(config), "--mode", "mock"],),
        (lambda config, tmp: ["sync", "--config", str(config), "--mode", "mock"],),
        (lambda config, tmp: ["check", "--config", str(config)],),
        (
            lambda config, tmp: [
                "plan",
                "--config",
                str(config),
                "--output",
                str(tmp / "plan.gplan"),
            ],
        ),
        (lambda config, tmp: ["preflight", "--config", str(config)],),
    ],
)
def test_invalid_authority_blocks_operational_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv_builder: object,
) -> None:
    _patch_env(monkeypatch)
    config = _write_invalid_authority_config(tmp_path)
    scan_calls = _spy_scan(monkeypatch)
    collibra_calls = _spy_collibra(monkeypatch)
    argv = argv_builder(config, tmp_path)  # type: ignore[operator]
    code = main(argv)
    assert code in {1, 4}
    assert scan_calls["scan"] == 0
    assert collibra_calls["adapter"] == 0
    assert collibra_calls["preflight"] == 0
    assert not (tmp_path / "out.json").exists()
    assert not (tmp_path / "plan.gplan").exists()


def test_invalid_authority_blocks_impact_before_provider_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_env(monkeypatch)
    config = _write_invalid_authority_config(tmp_path)
    changes = tmp_path / "changes.json"
    changes.write_text("{}", encoding="utf-8")
    calls = {"changes": 0, "odcs": 0, "dbt": 0, "ol": 0}

    def boom_changes(*_a: object, **_k: object) -> object:
        calls["changes"] += 1
        raise AssertionError("load_impact_changes must not run")

    def boom_odcs(*_a: object, **_k: object) -> object:
        calls["odcs"] += 1
        raise AssertionError("odcs loader must not run")

    def boom_dbt(*_a: object, **_k: object) -> object:
        calls["dbt"] += 1
        raise AssertionError("dbt loader must not run")

    def boom_ol(*_a: object, **_k: object) -> object:
        calls["ol"] += 1
        raise AssertionError("openlineage loader must not run")

    monkeypatch.setattr("governance.cli.load_impact_changes", boom_changes)
    monkeypatch.setattr("governance.cli.load_odcs_graph", boom_odcs)
    monkeypatch.setattr("governance.cli.load_dbt_graph", boom_dbt)
    monkeypatch.setattr("governance.cli.load_openlineage_graph", boom_ol)

    code = main(
        [
            "impact",
            "--namespace",
            "ns",
            "--changes",
            str(changes),
            "--odcs",
            str(tmp_path / "contract.yaml"),
            "--config",
            str(config),
            "--output",
            str(tmp_path / "impact.json"),
        ]
    )
    assert code in {1, 4}
    assert calls == {"changes": 0, "odcs": 0, "dbt": 0, "ol": 0}
    assert not (tmp_path / "impact.json").exists()


def test_apply_allows_saved_plan_read_then_aborts_before_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_env(monkeypatch)
    config = _write_invalid_authority_config(tmp_path)
    plan_path = tmp_path / "saved.gplan"
    plan_path.write_text("{}", encoding="utf-8")
    calls = {"plan": 0, "scan": 0, "adapter": 0}

    fake_plan = SimpleNamespace(
        target_context={},
        target_context_identity=SimpleNamespace(),
        config_identity=SimpleNamespace(),
        policy_identity=SimpleNamespace(),
        snapshot_identity=SimpleNamespace(),
        mapping_identity=SimpleNamespace(),
        remote_state_identity=SimpleNamespace(),
        sync_plan=SimpleNamespace(),
        content_identity=lambda: SimpleNamespace(),
    )

    def fake_load_saved_plan(path: object) -> object:
        calls["plan"] += 1
        assert Path(str(path)) == plan_path
        return fake_plan

    def boom_scan(_settings: object) -> object:
        calls["scan"] += 1
        raise AssertionError("scanner must not run")

    def boom_adapter(*_a: object, **_k: object) -> object:
        calls["adapter"] += 1
        raise AssertionError("Collibra must not run")

    monkeypatch.setattr("governance.cli.load_saved_plan", fake_load_saved_plan)
    monkeypatch.setattr("governance.cli._scan_model", boom_scan)
    monkeypatch.setattr("governance.cli.build_collibra_adapter", boom_adapter)

    code = main(["apply", str(plan_path), "--config", str(config), "--format", "json"])
    assert code in {1, 4}
    assert calls["plan"] == 1
    assert calls["scan"] == 0
    assert calls["adapter"] == 0


def test_config_validate_maps_ambiguity_to_indexed_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    authority = {
        "authority_schema": "governance-authority",
        "authority_version": "1",
        "rules": [
            {
                "id": "odcs-desc",
                "select": {"kind": "table", "property": "/description"},
                "authority": {"provider_type": "odcs"},
            },
            {
                "id": "dbt-desc",
                "select": {"kind": "table", "property": "/description"},
                "authority": {"provider_type": "dbt"},
            },
        ],
    }
    (tmp_path / "authority.yaml").write_text(
        yaml.safe_dump(authority, sort_keys=False),
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
                "authority:",
                "  files:",
                "    - authority.yaml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    code = main(["config", "validate", "--config", str(config), "--json"])
    assert code == 1
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostic_schema"] == "governance-config-diagnostics"
    paths = {item["path"] for item in payload["errors"]}
    assert paths == {"/authority/files/0"}
    assert all(item["code"] == "semantic_validation_failed" for item in payload["errors"])
    assert "action_contract_invalid" not in json.dumps(payload)

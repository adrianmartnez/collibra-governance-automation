"""Impact operation tests for the official GitHub Action orchestration."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from governance.github_ci.report import (
    build_impact_annotations,
    render_impact_report,
)
from governance.github_ci.result import IMPACT_RESULT_NAME, load_recognized_impact_result
from governance.github_ci.runner import (
    ActionInputContractError,
    parse_source_path_array,
    run_orchestration,
)
from governance.impact import ImpactIntegrityError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "github_action" / "impact"


def _read_github_output(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _impact_args(**overrides: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "config": "",
        "profile": "",
        "operation": "impact",
        "output_format": "human",
        "fail_on_policy_error": "true",
        "output_directory": ".governance",
        "plan_path": ".governance/governance.gplan",
        "pr_comment": "false",
        "impact_namespace": "analytics",
        "impact_changes": "changes.json",
        "impact_odcs": "",
        "impact_dbt_manifest": '["manifest.json"]',
        "impact_openlineage": "",
        "dbt_default_database": "",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _prepare_workspace(tmp_path: Path, *, impacted: bool = True) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    shutil.copy(FIXTURES / "changes.json", workspace / "changes.json")
    src = "manifest.impacted.json" if impacted else "manifest.clear.json"
    shutil.copy(FIXTURES / src, workspace / "manifest.json")
    return workspace


def test_parse_source_path_array_happy_path() -> None:
    assert parse_source_path_array(
        '["contracts/a.yaml","path with space/b.yaml","path,with,comma.yaml"]',
        field="impact-odcs",
    ) == [
        "contracts/a.yaml",
        "path with space/b.yaml",
        "path,with,comma.yaml",
    ]


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        '{"a":1}',
        '"scalar"',
        "[1]",
        '[""]',
        '["ok","bad\\npath"]',
        '["bad\\rpath"]',
        '["bad\\u0000path"]',
    ],
)
def test_parse_source_path_array_rejects_malformed(raw: str) -> None:
    with pytest.raises(ActionInputContractError):
        parse_source_path_array(raw, field="impact-odcs")


def test_parse_source_path_array_empty_means_none() -> None:
    assert parse_source_path_array("", field="impact-odcs") == []
    assert parse_source_path_array("[]", field="impact-odcs") == []


def test_impact_clear_success(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    buf = io.StringIO()
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(_impact_args(), stdout=buf)
    assert code == 0
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "false"
    assert values["status"] == "passed"
    assert values["impact-status"] == "clear"
    assert values["contract-version"] == ""
    assert values["result-path"] == ""
    assert values["impact-result-version"] == "1"
    assert values["impact-result-path"].endswith(f"/{IMPACT_RESULT_NAME}")
    assert values["writes-performed"] == "0"
    assert values["desired-exit-code"] == "0"
    assert (workspace / values["impact-result-path"]).is_file()
    assert (workspace / values["report-path"]).is_file()
    assert not (workspace / values["artifacts-path"] / "action-result.json").exists()
    payload = load_recognized_impact_result(workspace / values["impact-result-path"])
    assert payload["status"] == "clear"
    annotations = (workspace / values["annotations-path"]).read_text(encoding="utf-8")
    assert annotations == ""


def test_impact_impacted_exit_6_domain_success(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=True)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(_impact_args(), stdout=io.StringIO())
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "passed"
    assert values["impact-status"] == "impacted"
    assert values["desired-exit-code"] == "0"
    assert values["impact-result-version"] == "1"
    annotations = (workspace / values["annotations-path"]).read_text(encoding="utf-8")
    assert annotations.count("::warning::") == 1
    assert "governance impact: impacted;" in annotations
    assert "::error::" not in annotations


def test_impact_cli_validation_failure_materializes(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    shutil.copy(FIXTURES / "changes.invalid.json", workspace / "changes.json")
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(_impact_args(), stdout=io.StringIO())
    assert code == 0
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "false"
    assert values["status"] == "failed"
    assert values["impact-status"] == "failed"
    assert values["impact-result-version"] == ""
    assert values["desired-exit-code"] == "1"
    assert (workspace / values["report-path"]).is_file()
    annotations = (workspace / values["annotations-path"]).read_text(encoding="utf-8")
    assert annotations.count("::error::") == 1


def test_impact_missing_source_file_is_phase_b(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    shutil.copy(FIXTURES / "changes.json", workspace / "changes.json")
    # manifest.json intentionally absent but path is contained.
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(_impact_args(), stdout=io.StringIO())
    assert code == 0
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "false"
    assert values["status"] == "failed"
    assert values["impact-status"] == "failed"
    assert (workspace / ".governance").is_dir()
    assert (workspace / values["report-path"]).is_file()


def test_legacy_missing_config_is_phase_a_no_subprocess(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    calls: list[list[str]] = []

    def _fake_run(*args: Any, **kwargs: Any) -> Any:
        calls.append(list(args[0]) if args else [])
        raise AssertionError("subprocess should not run for Phase A missing config")

    args = _impact_args(operation="plan", config="", impact_namespace="")
    with (
        patch.dict(os.environ, env, clear=False),
        patch("governance.github_ci.runner.subprocess.run", side_effect=_fake_run),
    ):
        code = run_orchestration(args, stdout=io.StringIO())
    assert code == 0
    assert calls == []
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "true"
    assert values["contract-version"] == "1"
    assert values["desired-exit-code"] == "1"
    assert not (workspace / ".governance").exists()


def test_impact_duplicate_source_phase_a(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    args = _impact_args(
        impact_dbt_manifest='["manifest.json","manifest.json"]',
    )
    with (
        patch.dict(os.environ, env, clear=False),
        patch("governance.github_ci.runner.subprocess.run") as mocked,
    ):
        code = run_orchestration(args, stdout=io.StringIO())
    assert code == 0
    mocked.assert_not_called()
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "true"
    assert values["contract-version"] == ""
    assert not (workspace / ".governance").exists()


def test_impact_path_escape_phase_a(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    args = _impact_args(impact_dbt_manifest='["../outside.json"]')
    with (
        patch.dict(os.environ, env, clear=False),
        patch("governance.github_ci.runner.subprocess.run") as mocked,
    ):
        code = run_orchestration(args, stdout=io.StringIO())
    assert code == 0
    mocked.assert_not_called()
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "true"


def test_impact_hostile_shell_chars_treated_as_data(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    hostile = "path;$(echo pwned);id.json"
    (workspace / hostile).write_text("{}", encoding="utf-8")
    # Path won't be a valid manifest; ensure argv receives literal data and Phase B runs.
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    captured: list[list[str]] = []

    real_run = __import__("subprocess").run

    def _capture(argv: Any, **kwargs: Any) -> Any:
        captured.append(list(argv))
        return real_run(argv, **kwargs)

    args = _impact_args(impact_dbt_manifest=json.dumps([hostile]))
    with (
        patch.dict(os.environ, env, clear=False),
        patch("governance.github_ci.runner.subprocess.run", side_effect=_capture),
    ):
        code = run_orchestration(args, stdout=io.StringIO())
    assert code == 0
    assert captured
    assert hostile in captured[0]
    assert "$(echo pwned)" in " ".join(captured[0])
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "false"


def test_impact_source_order_determinism(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=True)
    second = workspace / "manifest-b.json"
    shutil.copy(workspace / "manifest.json", second)
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    output_directory = ".governance"
    env_base = {"GITHUB_WORKSPACE": str(workspace)}
    args_a = _impact_args(
        impact_dbt_manifest='["manifest.json","manifest-b.json"]',
        output_directory=output_directory,
    )
    args_b = _impact_args(
        impact_dbt_manifest='["manifest-b.json","manifest.json"]',
        output_directory=output_directory,
    )
    with patch.dict(os.environ, {**env_base, "GITHUB_OUTPUT": str(out_a)}, clear=False):
        assert run_orchestration(args_a, stdout=io.StringIO()) == 0
    artifact_a = (workspace / output_directory / IMPACT_RESULT_NAME).read_bytes()
    report_a = (workspace / output_directory / "report.md").read_bytes()
    shutil.rmtree(workspace / output_directory)
    with patch.dict(os.environ, {**env_base, "GITHUB_OUTPUT": str(out_b)}, clear=False):
        assert run_orchestration(args_b, stdout=io.StringIO()) == 0
    artifact_b = (workspace / output_directory / IMPACT_RESULT_NAME).read_bytes()
    report_b = (workspace / output_directory / "report.md").read_bytes()
    assert artifact_a == artifact_b
    assert report_a == report_b


def test_impact_tampered_artifact_action_contract_invalid(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=True)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }

    def _tamper(path: Path) -> dict[str, Any]:
        # Simulate post-write integrity failure from the authoritative loader.
        raise ImpactIntegrityError([])

    with (
        patch.dict(os.environ, env, clear=False),
        patch("governance.github_ci.runner.load_recognized_impact_result", side_effect=_tamper),
    ):
        code = run_orchestration(_impact_args(), stdout=io.StringIO())
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "failed"
    assert values["impact-status"] == "failed"
    assert values["desired-exit-code"] == "1"
    report = (workspace / values["report-path"]).read_text(encoding="utf-8")
    assert "action_contract_invalid" in report


def test_impact_profile_without_config_phase_a(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    args = _impact_args(profile="ci", config="")
    with (
        patch.dict(os.environ, env, clear=False),
        patch("governance.github_ci.runner.subprocess.run") as mocked,
    ):
        code = run_orchestration(args, stdout=io.StringIO())
    assert code == 0
    mocked.assert_not_called()
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "true"


def test_impact_annotations_clear_impacted_failed() -> None:
    assert build_impact_annotations(impact_status="clear") == []
    impacted = build_impact_annotations(
        impact_status="impacted",
        impact_result={
            "impact": {
                "direct_nodes": [{"kind": "table", "logical_id": "a"}],
                "transitive_nodes": [],
            },
            "affected_policies": [{"policy_id": "p"}],
        },
    )
    assert len(impacted) == 1
    assert impacted[0].startswith("::warning::")
    failed = build_impact_annotations(impact_status="failed", failure_code="configuration_failed")
    assert len(failed) == 1
    assert failed[0].startswith("::error::")


def test_impact_report_line_safety_and_caps() -> None:
    hostile = "table\nwith\rsep\u2028"
    nodes = [{"kind": "table", "logical_id": hostile, "namespace": "ns", "parent": None}]
    many = [
        {"kind": "table", "logical_id": f"t{i}", "namespace": "ns", "parent": None}
        for i in range(25)
    ]
    result = {
        "status": "impacted",
        "impact": {
            "changed_nodes": nodes,
            "direct_nodes": many,
            "transitive_nodes": [],
            "governance_assets": [],
            "associated_contracts": [],
            "paths": [],
        },
        "affected_policies": [],
    }
    text = render_impact_report(
        impact_status="impacted",
        impact_result=result,
        impact_result_path=".governance/impact-result.json",
        artifacts_relative=".governance",
    )
    assert "\nwith\r" not in text
    assert "showing 20 of 25; see .governance/impact-result.json" in text
    assert "provenance" not in text.lower()
    assert "attributes" not in text.lower()


def test_impact_token_scrubbed_from_subprocess_env(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    out = tmp_path / "github_output"
    summary = tmp_path / "step_summary.md"
    sentinel = "PR8_ENV_ONLY_SECRET_SENTINEL_6f94a2c1b8e047d3"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_TOKEN": sentinel,
        "GH_TOKEN": sentinel,
        "INPUT_GITHUB_TOKEN": sentinel,
        "GOVERNANCE_COMMENT_TOKEN": sentinel,
    }
    seen_env: dict[str, str] = {}
    seen_argv: list[str] = []

    real_run = __import__("subprocess").run

    def _capture(argv: Any, **kwargs: Any) -> Any:
        nonlocal seen_env, seen_argv
        seen_env = dict(kwargs.get("env") or {})
        seen_argv = list(argv)
        return real_run(argv, **kwargs)

    buf = io.StringIO()
    with (
        patch.dict(os.environ, env, clear=False),
        patch("governance.github_ci.runner.subprocess.run", side_effect=_capture),
    ):
        run_orchestration(_impact_args(), stdout=buf)
    assert "GITHUB_TOKEN" not in seen_env
    assert "GH_TOKEN" not in seen_env
    assert "INPUT_GITHUB_TOKEN" not in seen_env
    assert "GOVERNANCE_COMMENT_TOKEN" not in seen_env
    assert sentinel not in " ".join(seen_env.values())
    assert sentinel not in " ".join(seen_argv)
    assert sentinel not in buf.getvalue()
    assert sentinel not in out.read_text(encoding="utf-8")
    assert sentinel not in summary.read_text(encoding="utf-8")
    values = _read_github_output(out)
    impact_path = workspace / values["impact-result-path"]
    report_path = workspace / values["report-path"]
    annotations_path = workspace / values["annotations-path"]
    assert sentinel not in impact_path.read_text(encoding="utf-8")
    assert sentinel not in report_path.read_text(encoding="utf-8")
    assert sentinel not in annotations_path.read_text(encoding="utf-8")


def _fake_cli(
    *,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> Any:
    def _run(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return _run


def test_impact_exit1_empty_stdout_no_adhoc_json_envelope(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    buf = io.StringIO()
    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "governance.github_ci.runner.subprocess.run",
            side_effect=_fake_cli(returncode=1, stdout="", stderr="internal boom"),
        ),
    ):
        code = run_orchestration(
            _impact_args(output_format="json"),
            stdout=buf,
        )
    assert code == 0
    assert buf.getvalue() == ""
    values = _read_github_output(out)
    assert values["status"] == "failed"
    assert values["impact-status"] == "failed"
    assert values["desired-exit-code"] == "1"
    report = (workspace / values["report-path"]).read_text(encoding="utf-8")
    annotations = (workspace / values["annotations-path"]).read_text(encoding="utf-8")
    assert "operational_failure" in report
    assert annotations.count("::error::") == 1
    assert "internal boom" not in report
    assert "internal boom" not in annotations
    assert '"impact_status"' not in buf.getvalue()
    assert "writes_performed" not in buf.getvalue()


def test_impact_exit1_malformed_stdout_action_contract_invalid(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    bad = '{"not":"a-known-diagnostic","raw":"should-not-leak"}'
    buf = io.StringIO()
    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "governance.github_ci.runner.subprocess.run",
            side_effect=_fake_cli(returncode=1, stdout=bad, stderr="ignore-me"),
        ),
    ):
        code = run_orchestration(_impact_args(output_format="json"), stdout=buf)
    assert code == 0
    assert buf.getvalue() == ""
    values = _read_github_output(out)
    report = (workspace / values["report-path"]).read_text(encoding="utf-8")
    annotations = (workspace / values["annotations-path"]).read_text(encoding="utf-8")
    assert "action_contract_invalid" in report
    assert "should-not-leak" not in report
    assert "should-not-leak" not in annotations
    assert "ignore-me" not in report
    assert "ignore-me" not in annotations


def test_impact_exit4_malformed_stdout_action_contract_invalid(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=False)
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch(
            "governance.github_ci.runner.subprocess.run",
            side_effect=_fake_cli(returncode=4, stdout="{not-json", stderr="raw-stderr"),
        ),
    ):
        code = run_orchestration(_impact_args(output_format="json"), stdout=io.StringIO())
    assert code == 0
    values = _read_github_output(out)
    report = (workspace / values["report-path"]).read_text(encoding="utf-8")
    annotations = (workspace / values["annotations-path"]).read_text(encoding="utf-8")
    assert "action_contract_invalid" in report
    assert "raw-stderr" not in report
    assert "raw-stderr" not in annotations


def test_impact_step_summary_matches_report_bytes(tmp_path: Path) -> None:
    workspace = _prepare_workspace(tmp_path, impacted=True)
    out = tmp_path / "github_output"
    summary = tmp_path / "step_summary.md"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(_impact_args(), stdout=io.StringIO())
    assert code == 0
    values = _read_github_output(out)
    assert values["impact-status"] == "impacted"
    report_bytes = (workspace / values["report-path"]).read_bytes()
    summary_bytes = summary.read_bytes()
    assert summary_bytes == report_bytes

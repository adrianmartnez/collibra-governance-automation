"""Contract tests for the official Governance-as-Code GitHub Action."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest
import yaml

from governance.github_ci.finalize import _PUBLIC_COMMENT_STATUSES
from governance.github_ci.result import (
    CliContractError,
    canonical_json_text,
    parse_cli_payload,
    parse_known_diagnostic,
    parse_plan_document,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = REPO_ROOT / "action.yml"
SCHEMA_NAME = "governance-action-result.v1.schema.json"
SCHEMA_PATH = REPO_ROOT / "src" / "governance" / "github_ci" / "schemas" / SCHEMA_NAME
# governance-action-result v1 invariance pin (PR8 base 82192e5).
# Git blob (authoritative, identical at base and PR HEAD): 9e544262cbeebdc3421bbb21f83279c06afd0593
# Canonical LF content SHA-256 below; only CRLF→LF is normalized so Windows/Linux
# checkout EOL differences do not false-fail the pin.
EXPECTED_ACTION_RESULT_SCHEMA_CANONICAL_SHA256 = (
    "ceb4b853d00d976df0f872d914951f202308f92b1ac4d2a909ca2e5e7b96cf88"
)
EXPECTED_SETUP_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0"


def _canonical_schema_bytes(raw: bytes) -> bytes:
    """Normalize working-tree EOL only (CRLF → LF); no other transforms."""
    return raw.replace(b"\r\n", b"\n")


EXPECTED_INPUTS = {
    "config": {"required": False, "default": ""},
    "profile": {"required": False, "default": ""},
    "operation": {"required": False, "default": "plan"},
    "output-format": {"required": False, "default": "human"},
    "fail-on-policy-error": {"required": False, "default": "true"},
    "fail-on-review-blocked": {"required": False, "default": "true"},
    "output-directory": {"required": False, "default": ".governance"},
    "plan-path": {"required": False, "default": ".governance/governance.gplan"},
    "pr-comment": {"required": False, "default": "false"},
    "github-token": {"required": False, "default": ""},
    "impact-namespace": {"required": False, "default": ""},
    "impact-changes": {"required": False, "default": ""},
    "impact-odcs": {"required": False, "default": ""},
    "impact-dbt-manifest": {"required": False, "default": ""},
    "impact-openlineage": {"required": False, "default": ""},
    "dbt-default-database": {"required": False, "default": ""},
    "review-observations": {"required": False, "default": ""},
    "review-comparison": {"required": False, "default": ""},
    "review-baseline-snapshot": {"required": False, "default": ""},
    "review-candidate-snapshot": {"required": False, "default": ""},
    "review-drift-policy": {"required": False, "default": ""},
    "review-align-source-roots": {"required": False, "default": "false"},
    "review-align-database-roots": {"required": False, "default": "false"},
}

EXPECTED_OUTPUTS = (
    "contract-version",
    "status",
    "validation-status",
    "policy-status",
    "policy-violation-count",
    "policy-error-count",
    "policy-warning-count",
    "plan-status",
    "create-count",
    "update-count",
    "unchanged-count",
    "remote-only-count",
    "writes-performed",
    "plan-path",
    "result-path",
    "report-path",
    "artifacts-path",
    "comment-status",
    "impact-status",
    "impact-result-path",
    "impact-result-version",
    "review-status",
    "review-result-path",
    "review-result-version",
    "conflict-status",
    "conflict-property-count",
    "unresolved-conflict-count",
    "resolved-authority-count",
    "reconciliation-blocked-count",
    "drift-status",
    "expected-difference-count",
    "unexpected-drift-count",
    "drift-affected-object-count",
    "comparison-result-path",
    "drift-result-path",
)

ORCHESTRATION_ENV_KEYS = (
    "GOV_ACTION_CONFIG",
    "GOV_ACTION_PROFILE",
    "GOV_ACTION_OPERATION",
    "GOV_ACTION_OUTPUT_FORMAT",
    "GOV_ACTION_FAIL_ON_POLICY_ERROR",
    "GOV_ACTION_FAIL_ON_REVIEW_BLOCKED",
    "GOV_ACTION_OUTPUT_DIRECTORY",
    "GOV_ACTION_PLAN_PATH",
    "GOV_ACTION_PR_COMMENT",
    "GOV_ACTION_IMPACT_NAMESPACE",
    "GOV_ACTION_IMPACT_CHANGES",
    "GOV_ACTION_IMPACT_ODCS",
    "GOV_ACTION_IMPACT_DBT_MANIFEST",
    "GOV_ACTION_IMPACT_OPENLINEAGE",
    "GOV_ACTION_DBT_DEFAULT_DATABASE",
    "GOV_ACTION_REVIEW_OBSERVATIONS",
    "GOV_ACTION_REVIEW_COMPARISON",
    "GOV_ACTION_REVIEW_BASELINE_SNAPSHOT",
    "GOV_ACTION_REVIEW_CANDIDATE_SNAPSHOT",
    "GOV_ACTION_REVIEW_DRIFT_POLICY",
    "GOV_ACTION_REVIEW_ALIGN_SOURCE_ROOTS",
    "GOV_ACTION_REVIEW_ALIGN_DATABASE_ROOTS",
)


def _load_action() -> dict:
    return yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))


def _action_text() -> str:
    return ACTION_YML.read_text(encoding="utf-8")


def _load_schema() -> dict:
    text = files("governance.github_ci.schemas").joinpath(SCHEMA_NAME).read_text(encoding="utf-8")
    return json.loads(text)


def test_action_yml_is_composite() -> None:
    action = _load_action()
    assert action["runs"]["using"] == "composite"
    steps = action["runs"]["steps"]
    assert isinstance(steps, list)
    assert len(steps) >= 5
    ids = [step.get("id") for step in steps if "id" in step]
    assert "bootstrap" in ids
    assert "governance-run" in ids
    assert "comment-state" in ids
    assert "final" in ids


def test_action_yml_exact_inputs_and_defaults() -> None:
    action = _load_action()
    inputs = action["inputs"]
    assert set(inputs) == set(EXPECTED_INPUTS)
    for name, expected in EXPECTED_INPUTS.items():
        assert inputs[name]["required"] is expected["required"]
        if "default" in expected:
            assert inputs[name].get("default") == expected["default"]


def test_action_yml_outputs_map_only_to_steps_final() -> None:
    action = _load_action()
    outputs = action["outputs"]
    assert tuple(outputs) == EXPECTED_OUTPUTS
    for name, spec in outputs.items():
        value = spec["value"]
        assert value == f"${{{{ steps.final.outputs.{name} }}}}"
        assert "steps.governance-run" not in value
        assert "steps.comment-state" not in value
        assert "steps.governance-comment" not in value


def test_action_yml_has_no_apply_or_secret_provider_inputs() -> None:
    action = _load_action()
    names = set(action["inputs"])
    forbidden = {
        "apply",
        "sync",
        "database-url",
        "collibra-token",
        "collibra-password",
        "collibra-username",
        "secret-provider",
        "provider-credentials",
    }
    assert names.isdisjoint(forbidden)
    text = _action_text().lower()
    assert "apply" not in action["inputs"]
    assert "secret-provider" not in text


def test_pr_comment_default_false() -> None:
    action = _load_action()
    assert action["inputs"]["pr-comment"]["default"] == "false"


def test_setup_python_pinned_to_sha_with_version_comment() -> None:
    text = _action_text()
    assert EXPECTED_SETUP_PYTHON in text
    action = _load_action()
    setup = action["runs"]["steps"][0]
    assert setup["uses"].startswith("actions/setup-python@")
    assert "a26af69be951a213d495a4c3e4e4022e16d87065" in setup["uses"]


def test_action_yml_bootstrap_uses_fresh_unique_venv() -> None:
    text = _action_text()
    assert 'mktemp -d "${RUNNER_TEMP}/gac-action-venv.XXXXXX"' in text
    assert "ACTION_VENV=" in text
    assert "mktemp -d" in text
    assert "XXXXXX" in text
    assert "/tmp/gac-action-venv" not in text
    assert 'RUNNER_TEMP}/gac-action-venv"' not in text  # fixed path without XXXXXX
    assert "python -m venv" in text
    assert "${{ github.action_path }}" in text


def test_schema_packaged_via_importlib_resources() -> None:
    resource = files("governance.github_ci.schemas").joinpath(SCHEMA_NAME)
    assert resource.is_file()
    schema = _load_schema()
    assert schema["$id"] == ("urn:collibra-governance-automation:schema:governance-action-result:1")


def test_action_result_schema_bytes_and_sha_preserved() -> None:
    raw = SCHEMA_PATH.read_bytes()
    canonical = _canonical_schema_bytes(raw)
    assert hashlib.sha256(canonical).hexdigest() == EXPECTED_ACTION_RESULT_SCHEMA_CANONICAL_SHA256
    schema = json.loads(canonical.decode("utf-8"))
    assert schema["$id"] == ("urn:collibra-governance-automation:schema:governance-action-result:1")
    assert schema["properties"]["operation"]["enum"] == ["validate", "check", "plan"]
    assert "impact" not in schema["properties"]["operation"]["enum"]
    assert "review" not in schema["properties"]["operation"]["enum"]


def test_action_result_schema_canonical_sha_ignores_crlf_checkout() -> None:
    raw = SCHEMA_PATH.read_bytes()
    lf = _canonical_schema_bytes(raw)
    crlf = lf.replace(b"\n", b"\r\n")
    assert hashlib.sha256(_canonical_schema_bytes(lf)).hexdigest() == (
        EXPECTED_ACTION_RESULT_SCHEMA_CANONICAL_SHA256
    )
    assert hashlib.sha256(_canonical_schema_bytes(crlf)).hexdigest() == (
        EXPECTED_ACTION_RESULT_SCHEMA_CANONICAL_SHA256
    )


def test_schema_id_urn_and_enums_exclude_reporting_failure() -> None:
    schema = _load_schema()
    assert schema["$id"] == ("urn:collibra-governance-automation:schema:governance-action-result:1")
    assert schema["properties"]["result_schema"]["const"] == "governance-action-result"
    assert schema["properties"]["result_version"]["const"] == "1"
    assert schema["properties"]["status"]["enum"] == ["passed", "blocked", "failed"]
    assert schema["properties"]["operation"]["enum"] == ["validate", "check", "plan"]
    failure_codes = schema["properties"]["failure_code"]["enum"]
    assert None in failure_codes
    assert "reporting_failure" not in failure_codes
    assert set(failure_codes) == {
        None,
        "configuration_failed",
        "policy_blocked",
        "operational_failure",
        "inputs_changed_during_run",
        "action_contract_invalid",
    }
    assert schema["properties"]["execution"]["properties"]["status"]["const"] == "not_requested"
    assert schema["properties"]["execution"]["properties"]["writes_performed"]["const"] == 0


def test_public_comment_status_never_requested() -> None:
    assert "requested" not in _PUBLIC_COMMENT_STATUSES
    assert (
        frozenset(
            {
                "disabled",
                "created",
                "updated",
                "skipped_non_pr",
                "skipped_untrusted_fork",
                "failed",
            }
        )
        == _PUBLIC_COMMENT_STATUSES
    )


def test_runner_steps_use_isolated_python_dash_i() -> None:
    text = _action_text()
    assert "-I -m governance.github_ci run" in text
    assert "-I -m governance.github_ci comment" in text
    assert "-I -m governance.github_ci finalize" in text
    assert "-I -m governance.github_ci fail-gate" in text
    action = _load_action()
    run_step = next(step for step in action["runs"]["steps"] if step.get("id") == "governance-run")
    env = run_step.get("env") or {}
    assert "GITHUB_TOKEN" not in env
    assert "GH_TOKEN" not in env
    assert "INPUT_GITHUB_TOKEN" not in env
    assert "Intentionally omit" in _action_text()


def test_orchestration_uses_env_transport_not_input_interpolation_in_argv() -> None:
    action = _load_action()
    run_step = next(step for step in action["runs"]["steps"] if step.get("id") == "governance-run")
    env = run_step.get("env") or {}
    for key in ORCHESTRATION_ENV_KEYS:
        assert key in env
    run_script = run_step["run"]
    assert "${{ inputs." not in run_script
    assert '--impact-odcs "$GOV_ACTION_IMPACT_ODCS"' in run_script
    assert '--config "$GOV_ACTION_CONFIG"' in run_script
    assert "contract-version" in action["outputs"]
    assert "empty for impact" in action["outputs"]["contract-version"]["description"].lower()


def _plan_payload(*, version: str) -> dict:
    identity = {
        "algorithm": "sha256",
        "digest": "a" * 64,
        "hashing_contract_version": "1",
    }
    return {
        "plan_schema": "governance-plan",
        "plan_version": version,
        "actions": [{"action_type": "create", "local_id": "table:demo/db/public/t"}],
        "config_identity": identity,
        "policy_identity": identity,
        "snapshot_identity": identity,
    }


def test_parse_plan_document_accepts_v1_and_v2() -> None:
    for version in ("1", "2"):
        parsed = parse_plan_document(canonical_json_text(_plan_payload(version=version)))
        assert parsed["plan_version"] == version


def test_parse_plan_document_rejects_v3() -> None:
    with pytest.raises(CliContractError, match="unsupported plan_version"):
        parse_plan_document(canonical_json_text(_plan_payload(version="3")))


def test_parse_cli_payload_expect_plan_accepts_v2() -> None:
    payload = parse_cli_payload(
        canonical_json_text(_plan_payload(version="2")),
        expect="plan",
    )
    assert payload["plan_version"] == "2"


def test_parse_cli_payload_plan_or_policy_or_diagnostic_accepts_v2() -> None:
    payload = parse_cli_payload(
        canonical_json_text(_plan_payload(version="2")),
        expect="plan-or-policy-or-diagnostic",
    )
    assert payload["plan_version"] == "2"


def test_parse_known_diagnostic_accepts_reconciliation_diagnostics() -> None:
    diagnostic = {
        "diagnostic_schema": "governance-reconciliation-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [
            {
                "code": "unresolved_property_conflict",
                "path": "/objects/x",
                "message": "blocked",
            }
        ],
    }
    parsed = parse_known_diagnostic(canonical_json_text(diagnostic))
    assert parsed["diagnostic_schema"] == "governance-reconciliation-diagnostics"


def test_parse_known_diagnostic_rejects_reconciliation_version_not_1() -> None:
    diagnostic = {
        "diagnostic_schema": "governance-reconciliation-diagnostics",
        "diagnostic_version": "2",
        "ok": False,
        "errors": [{"code": "source_error", "path": "/sources/odcs/0", "message": "bad"}],
    }
    with pytest.raises(CliContractError, match="unexpected diagnostic_version"):
        parse_known_diagnostic(canonical_json_text(diagnostic))

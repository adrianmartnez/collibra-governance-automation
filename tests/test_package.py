"""Package import and entry-point smoke tests."""

from __future__ import annotations

import subprocess
import sys
from importlib import metadata
from importlib.resources import files
from pathlib import Path

import governance
from governance import __version__


def test_package_import() -> None:
    assert __version__ == "1.4.0"
    assert governance.__version__ == "1.4.0"
    assert governance.__name__ == "governance"


def test_pyproject_version_matches_runtime() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'version = "1.4.0"' in text
    assert metadata.version("collibra-governance-automation") == "1.4.0"


def test_history_observation_snapshot_schemas_packaged() -> None:
    hist = files("governance.history.schemas").joinpath("governance-history.v1.schema.json")
    hdiag = files("governance.history.schemas").joinpath(
        "governance-history-diagnostics.v1.schema.json"
    )
    hevo = files("governance.history.schemas").joinpath(
        "governance-history-evolution.v1.schema.json"
    )
    obs = files("governance.observations.schemas").joinpath(
        "governance-property-observations.v1.schema.json"
    )
    snap = files("governance.snapshots.schemas").joinpath("governance-snapshot.v1.schema.json")
    for resource in (hist, hdiag, hevo, obs, snap):
        assert resource.is_file()
        text = resource.read_text(encoding="utf-8")
        assert "urn:collibra-governance-automation:schema:" in text


def test_review_schema_packaged_and_module_imports() -> None:
    resource = files("governance.github_ci.schemas").joinpath(
        "governance-ci-review-result.v1.schema.json"
    )
    assert resource.is_file()
    text = resource.read_text(encoding="utf-8")
    assert "urn:collibra-governance-automation:schema:governance-ci-review-result:1" in text
    import governance.github_ci.review as review

    assert review.REVIEW_SCHEMA == "governance-ci-review-result"
    assert review.REVIEW_VERSION == "1"


def test_authority_impact_reconciliation_plan_v2_schemas_packaged() -> None:
    resources = (
        files("governance.authority.schemas").joinpath("governance-authority.v1.schema.json"),
        files("governance.plans.schemas").joinpath("governance-plan.v2.schema.json"),
        files("governance.impact.schemas").joinpath("governance-impact-changes.v1.schema.json"),
        files("governance.impact.schemas").joinpath("governance-impact-result.v1.schema.json"),
        files("governance.reconciliation.schemas").joinpath(
            "governance-explain-result.v1.schema.json"
        ),
        files("governance.reconciliation.schemas").joinpath(
            "governance-explain-diagnostics.v1.schema.json"
        ),
        files("governance.reconciliation.schemas").joinpath(
            "governance-reconciliation-diagnostics.v1.schema.json"
        ),
    )
    expected_ids = (
        "urn:collibra-governance-automation:schema:governance-authority:1",
        "urn:collibra-governance-automation:schema:governance-plan:2",
        "urn:collibra-governance-automation:schema:governance-impact-changes:1",
        "urn:collibra-governance-automation:schema:governance-impact-result:1",
        "urn:collibra-governance-automation:schema:governance-explain-result:1",
        "urn:collibra-governance-automation:schema:governance-explain-diagnostics:1",
        "urn:collibra-governance-automation:schema:governance-reconciliation-diagnostics:1",
    )
    for resource, expected in zip(resources, expected_ids, strict=True):
        assert resource.is_file()
        assert expected in resource.read_text(encoding="utf-8")


def test_sample_drift_policy_loads() -> None:
    from governance.drift.load import load_drift_policy

    root = Path(__file__).resolve().parents[1]
    policy = load_drift_policy(root / "sample/drift/governance-drift-policy.example.yaml")
    assert len(policy.rules) == 2
    assert policy.rules[0].id == "allow-source-description"
    assert policy.rules[1].id == "allow-customers-table-description"


def test_sample_governance_example_validates() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "governance",
            "config",
            "validate",
            "--config",
            str(root / "sample/governance.example.yaml"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_readme_documents_both_mutation_lanes() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "--apply" in readme
    assert "--confirm-live" in readme
    assert "governance sync --mode mock --apply" in readme
    assert "governance apply FILE.gplan" in readme
    assert "Only explicit reviewed plans proceed to reconciliation" not in readme
    assert "safe reconciliation only from explicit reviewed plans" not in readme
    assert "snapshot compare -> drift classification -> local history" not in readme
    assert "Drift --> History" not in readme
    assert "Snapshots --> History" in readme
    assert "Comparison --> Drift" in readme
    assert "Snapshot-only history works without context" in readme
    assert "does not consume `governance-drift-result`" in readme


def test_entry_point_module_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "governance", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "scan" in result.stdout
    assert "export" in result.stdout
    assert "diff" in result.stdout
    assert "sync" in result.stdout
    assert "config" in result.stdout
    assert "check" in result.stdout
    assert "plan" in result.stdout
    assert "apply" in result.stdout
    assert "preflight" in result.stdout
    assert "compare" in result.stdout
    assert "drift" in result.stdout
    assert "history" in result.stdout
    assert "password=" not in result.stdout


def test_entry_point_module_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "governance", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"governance {__version__}"

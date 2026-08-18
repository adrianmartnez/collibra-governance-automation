"""Governance CLI: scan, export, diff, sync, check, plan, apply, and impact."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, TextIO

from governance import __version__
from governance.config import Settings, load_settings
from governance.config_contract import (
    CanonicalConfig,
    ConfigContractError,
    ConfigResolutionError,
    diagnostics_failure,
    diagnostics_success,
    load_canonical_config,
    resolve_inventory_path,
    resolve_mapping_path,
    resolve_settings,
    resolve_snapshot_path,
    runtime_invalid_diagnostic,
    unresolved_env_diagnostic,
    validate_collibra_runtime,
    validate_governance_config,
)
from governance.config_contract.resolution_diagnostics import CODE_ENV_UNRESOLVED
from governance.domain import GovernanceGraph, GovernanceModel
from governance.domain.impact import analyze_downstream_impact
from governance.exporters import (
    SCANNER_CONTRACT_VERSION,
    InventoryExportError,
    MetadataInventory,
    write_inventory,
)
from governance.identity import (
    config_identity,
    mapping_identity,
    policy_identity,
    target_context_identity,
)
from governance.impact import (
    CODE_CHANGED_NODE,
    CODE_GRAPH_CONFLICT,
    CODE_SOURCE,
    ImpactChangedNodeError,
    ImpactDiagnosticError,
    ImpactError,
    ImpactGraphConflictError,
    ImpactSourceError,
    build_impact_result,
    canonical_impact_json,
    format_human_value,
    format_impact_result_human,
    impact_diagnostics_failure,
    load_impact_changes,
    match_affected_policies,
    write_impact_result,
)
from governance.integrations.collibra import (
    PLANNER_CONTRACT_VERSION,
    CollibraAdapterError,
    CollibraMappingConfig,
    CollibraMappingError,
    ImportExecutionResult,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    SyncResult,
    build_collibra_adapter,
    build_sync_plan,
    execute_collibra_plan,
    load_mapping_config_file,
    map_to_desired_state,
    mapping_contains_example_placeholders,
    mock_mapping_config,
)
from governance.integrations.dbt import DbtError, load_dbt_graph
from governance.integrations.odcs import OdcsError, load_odcs_graph
from governance.integrations.openlineage import OpenLineageError, load_openlineage_graph
from governance.operation_diagnostics import operation_diagnostics_failure
from governance.plans import (
    PlanError,
    SavedGovernancePlan,
    build_apply_result,
    build_import_submission_result,
    build_import_sync_payload,
    build_saved_plan,
    build_stale_result,
    compute_remote_state_identity_value,
    format_apply_result_human,
    format_import_submission_human,
    format_import_sync_human,
    format_stale_human,
    identity_mismatch,
    load_saved_plan,
    plan_diagnostics_failure,
    version_mismatch,
    write_saved_plan,
)
from governance.plans.errors import (
    CODE_TARGET_CONTEXT_INCONSISTENT,
    PlanDiagnosticError,
    PlanIntegrityError,
)
from governance.plans.target_context import (
    build_target_context_projection,
    target_context_public,
)
from governance.policy import (
    PolicyError,
    build_policy_report,
    evaluate_policies,
    format_policy_report_human,
    load_normalized_policies,
    policy_diagnostics_failure,
)
from governance.scanner import MetadataDiscoveryError, PostgresMetadataScanner
from governance.snapshots import GovernanceSnapshot

Mode = Literal["mock", "live"]
ArtifactKind = Literal["inventory", "snapshot"]
OutputFormat = Literal["human", "json"]

_SAFE_UNEXPECTED = "unexpected error"
_SAFE_MAPPING = "invalid Collibra mapping configuration"
_SAFE_PLACEHOLDER = "Collibra mapping configuration contains example placeholders"
_SAFE_MAPPING_REQUIRED = "live mode requires --mapping-config"
_SAFE_MAPPING_REQUIRED_GAC = "live mode requires a Collibra mapping path in governance.yaml"
_SAFE_SYNC_FAILED = "synchronization failed"
_SAFE_TARGET_REQUIRED = "governance.yaml must define a target for diff/sync"
_SAFE_TARGET_REQUIRED_PLAN = "governance.yaml must define a target for plan/apply"
_SAFE_CONFIG = "invalid governance configuration"
_SAFE_RESOLUTION = "required environment reference could not be resolved"

_OPERATIONAL_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ValueError,
    MetadataDiscoveryError,
    InventoryExportError,
    CollibraMappingError,
    CollibraAdapterError,
)


class CliUsageError(Exception):
    """CLI flag combination error (exit code 2, no operational I/O)."""


class CliOperationalError(Exception):
    """Expected operational failure translated to a safe stderr message."""


def _is_operational_error(exc: BaseException) -> bool:
    # Runtime configuration failures must not be classified as operational I/O.
    if isinstance(exc, ConfigResolutionError):
        return False
    if isinstance(exc, _OPERATIONAL_ERROR_TYPES):
        return True
    try:
        from governance.snapshots.errors import SnapshotError
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, SnapshotError)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    try:
        return _run(list(argv) if argv is not None else None)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        return 1
    except CliUsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except CliOperationalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ConfigContractError as exc:
        print(f"error: {_SAFE_CONFIG}", file=sys.stderr)
        for item in exc.errors:
            print(f"  {item.path or '/'}: {item.message} ({item.code})", file=sys.stderr)
        return 1
    except Exception as exc:
        if _is_operational_error(exc):
            print(f"error: {_safe_error_message(exc)}", file=sys.stderr)
            return 1
        print(f"error: {_SAFE_UNEXPECTED}", file=sys.stderr)
        return 1


def _run(argv: list[str] | None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stdout)
        return 0

    command = args.command
    if command == "config":
        return _cmd_config(args)
    if command == "check":
        return _cmd_check(args)
    if command == "plan":
        return _cmd_plan(args)
    if command == "apply":
        return _cmd_apply(args)
    if command == "impact":
        return _cmd_impact(args)

    canonical: CanonicalConfig | None = None
    config_path = getattr(args, "config", None)
    if config_path is not None:
        canonical = load_canonical_config(
            config_path,
            profile=getattr(args, "profile", None),
        )
        if command in {"diff", "sync"} and not canonical.targets:
            raise CliOperationalError(_SAFE_TARGET_REQUIRED)
        settings = resolve_settings(canonical)
    else:
        settings = load_settings()

    if command == "scan":
        return _cmd_scan(settings, json_output=bool(args.json))
    if command == "export":
        artifact: ArtifactKind = getattr(args, "artifact", "inventory") or "inventory"
        output = _resolve_export_output(
            settings,
            canonical=canonical,
            artifact=artifact,
            cli_output=getattr(args, "output", None),
        )
        return _cmd_export(settings, output_path=output, artifact=artifact)
    if command == "diff":
        mode = _resolve_mode(settings, getattr(args, "mode", None))
        mapping_path = _resolve_cli_mapping_path(
            canonical,
            getattr(args, "mapping_config", None),
        )
        return _cmd_diff(
            settings,
            mode=mode,
            mapping_config_path=mapping_path,
            json_output=bool(args.json),
        )
    if command == "sync":
        mode = _resolve_mode(settings, getattr(args, "mode", None))
        apply = bool(getattr(args, "apply", False))
        confirm_live = bool(getattr(args, "confirm_live", False))
        _validate_sync_flags(mode=mode, apply=apply, confirm_live=confirm_live)
        mapping_path = _resolve_cli_mapping_path(
            canonical,
            getattr(args, "mapping_config", None),
        )
        return _cmd_sync(
            settings,
            mode=mode,
            mapping_config_path=mapping_path,
            apply=apply,
            json_output=bool(args.json),
        )
    parser.error(f"unknown command: {command}")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance",
        description=(
            "Discover PostgreSQL technical metadata, export inventory, evaluate "
            "policies, and run plan-driven Collibra diff/sync/apply. Dry-run means "
            "zero remote mutations (live mode may still perform GET reads)."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"governance {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser(
        "scan",
        help="Discover PostgreSQL metadata and print a summary (no Collibra I/O).",
    )
    _add_config_profile(scan)
    scan.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable summary JSON object on stdout.",
    )

    export = subparsers.add_parser(
        "export",
        help="Discover metadata and write deterministic inventory or snapshot JSON.",
    )
    _add_config_profile(export)
    export.add_argument(
        "--artifact",
        choices=("inventory", "snapshot"),
        default="inventory",
        help="Artifact kind to write (default: inventory).",
    )
    export.add_argument(
        "--output",
        metavar="PATH",
        help="Output path (default: settings or governance.yaml artifacts paths).",
    )

    diff = subparsers.add_parser(
        "diff",
        help=(
            "Build a sync plan against remote managed state without writes. "
            "In live mode this reads remote state (GET); mutations remain zero."
        ),
    )
    _add_config_profile(diff)
    _add_mode_mapping_json(diff)

    sync = subparsers.add_parser(
        "sync",
        help=(
            "Plan and optionally apply metadata synchronization. "
            "Default is dry-run (zero remote mutations). Live apply requires "
            "--apply and --confirm-live."
        ),
    )
    _add_config_profile(sync)
    _add_mode_mapping_json(sync)
    sync.add_argument(
        "--apply",
        action="store_true",
        help="Apply planned CREATE/UPDATE writes (default: dry-run, applied=0).",
    )
    sync.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required together with --apply when effective mode is live.",
    )

    check = subparsers.add_parser(
        "check",
        help="Evaluate governance policies against a scanned snapshot (no Collibra I/O).",
    )
    check.add_argument(
        "--config",
        metavar="PATH",
        required=True,
        help="Path to governance.yaml (required).",
    )
    check.add_argument(
        "--profile",
        metavar="NAME",
        help="Named profile overlay (overrides GOVERNANCE_PROFILE).",
    )
    _add_format(check)

    plan = subparsers.add_parser(
        "plan",
        help="Generate or inspect a saved governance plan (.gplan).",
    )
    plan_sub = plan.add_subparsers(dest="plan_command")
    plan_sub.required = False
    plan.add_argument(
        "--config",
        metavar="PATH",
        help="Path to governance.yaml (required for plan generation).",
    )
    plan.add_argument(
        "--profile",
        metavar="NAME",
        help="Named profile overlay when generating a plan.",
    )
    plan.add_argument(
        "--output",
        metavar="FILE",
        help="Output .gplan path (required for plan generation).",
    )
    _add_format(plan)

    inspect = plan_sub.add_parser(
        "inspect",
        help="Inspect a saved .gplan without PostgreSQL or Collibra I/O.",
    )
    inspect.add_argument(
        "plan_file",
        metavar="FILE",
        help="Path to a saved .gplan artifact.",
    )
    _add_format(inspect)

    apply = subparsers.add_parser(
        "apply",
        help=(
            "Validate freshness of a saved .gplan and optionally execute saved actions. "
            "Default is dry-run (zero remote mutations)."
        ),
    )
    apply.add_argument(
        "plan_file",
        metavar="FILE",
        help="Path to a saved .gplan artifact.",
    )
    apply.add_argument(
        "--config",
        metavar="PATH",
        required=True,
        help="Path to governance.yaml (required).",
    )
    apply.add_argument(
        "--profile",
        metavar="NAME",
        help="Named profile overlay (overrides GOVERNANCE_PROFILE).",
    )
    _add_format(apply)
    apply.add_argument(
        "--apply",
        action="store_true",
        help="Execute saved CREATE/UPDATE writes (default: dry-run).",
    )
    apply.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required together with --apply when effective mode is live.",
    )

    config = subparsers.add_parser(
        "config",
        help="Governance-as-Code configuration utilities.",
    )
    config_sub = config.add_subparsers(dest="config_command")
    validate = config_sub.add_parser(
        "validate",
        help="Validate governance.yaml (no PostgreSQL or Collibra I/O).",
    )
    validate.add_argument(
        "--config",
        metavar="PATH",
        default="governance.yaml",
        help="Path to governance.yaml (default: governance.yaml).",
    )
    validate.add_argument(
        "--profile",
        metavar="NAME",
        help="Named profile overlay (overrides GOVERNANCE_PROFILE).",
    )
    validate.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable diagnostics JSON on stdout.",
    )

    impact = subparsers.add_parser(
        "impact",
        help=(
            "Analyze deterministic downstream governance impact from ODCS/dbt/"
            "OpenLineage graphs (zero remote writes)."
        ),
    )
    impact.add_argument(
        "--namespace",
        metavar="NAME",
        required=True,
        help="Shared logical namespace for all source graphs and changed nodes.",
    )
    impact.add_argument(
        "--changes",
        metavar="FILE",
        required=True,
        help="Path to governance-impact-changes v1 JSON input.",
    )
    impact.add_argument(
        "--output",
        metavar="FILE",
        required=True,
        help="Path for the canonical governance-impact-result JSON artifact.",
    )
    impact.add_argument(
        "--odcs",
        metavar="PATH",
        action="append",
        default=None,
        help="ODCS data-contract path (repeatable).",
    )
    impact.add_argument(
        "--dbt-manifest",
        metavar="PATH",
        action="append",
        default=None,
        help="dbt manifest.json path (repeatable).",
    )
    impact.add_argument(
        "--openlineage",
        metavar="PATH",
        action="append",
        default=None,
        help="OpenLineage events JSON path (repeatable).",
    )
    impact.add_argument(
        "--dbt-default-database",
        metavar="NAME",
        help="Optional default database fallback for every dbt manifest loader.",
    )
    impact.add_argument(
        "--config",
        metavar="PATH",
        help="Optional governance.yaml used only to load configured policies.",
    )
    impact.add_argument(
        "--profile",
        metavar="NAME",
        help="Named profile overlay; valid only with --config.",
    )
    _add_format(impact)
    return parser


def _add_config_profile(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Optional governance.yaml path (opt-in GaC mode; no auto-discovery).",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        help="Named profile overlay when --config is set (overrides GOVERNANCE_PROFILE).",
    )


def _add_mode_mapping_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=("mock", "live"),
        help="Override COLLIBRA_MODE (default: settings / mock).",
    )
    parser.add_argument(
        "--mapping-config",
        metavar="PATH",
        help="JSON Collibra mapping refs file (required in live mode).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON on stdout.",
    )


def _add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="Output format (default: human).",
    )


def _cmd_config(args: argparse.Namespace) -> int:
    if getattr(args, "config_command", None) != "validate":
        raise CliUsageError("usage: governance config validate [--config PATH]")
    config_path = Path(args.config)
    json_output = bool(args.json)
    try:
        _canonical, identity = validate_governance_config(
            config_path,
            profile=getattr(args, "profile", None),
        )
    except ConfigContractError as exc:
        payload = diagnostics_failure(exc.errors)
        if json_output:
            _print_json(payload)
        else:
            print("ok=false")
            for item in exc.errors:
                print(f"error path={item.path or '/'} code={item.code} message={item.message}")
        return 1

    payload = diagnostics_success(identity)
    if json_output:
        _print_json(payload)
    else:
        print("ok=true")
        print(f"algorithm={identity['algorithm']}")
        print(f"hashing_contract_version={identity['hashing_contract_version']}")
        print(f"digest={identity['digest']}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    fmt: OutputFormat = args.format
    loaded = _load_canonical_and_settings(
        config_path=args.config,
        profile=getattr(args, "profile", None),
        fmt=fmt,
    )
    if isinstance(loaded, int):
        return loaded
    canonical, settings = loaded

    try:
        policy_set = load_normalized_policies(canonical)
    except PolicyError as exc:
        return _emit_policy_error(exc, fmt)

    try:
        model = _scan_model(settings)
    except Exception as exc:
        if _is_operational_error(exc):
            return _emit_operational(exc, fmt)
        raise

    snapshot = GovernanceSnapshot.from_model(model)
    violations = evaluate_policies(model, policy_set)
    report = build_policy_report(
        violations=violations,
        policy_identity=policy_identity(policy_set.to_identity_dict()),
        snapshot_identity=snapshot.content_identity(),
    )
    if fmt == "json":
        _print_json(report)
    else:
        sys.stdout.write(format_policy_report_human(report))
    return 0 if report["ok"] else 3


def _cmd_plan(args: argparse.Namespace) -> int:
    if getattr(args, "plan_command", None) == "inspect":
        return _cmd_plan_inspect(args)
    return _cmd_plan_generate(args)


def _cmd_plan_inspect(args: argparse.Namespace) -> int:
    fmt: OutputFormat = args.format
    try:
        plan = load_saved_plan(args.plan_file)
    except PlanError as exc:
        return _emit_plan_error(exc, fmt)

    if fmt == "json":
        _print_json(plan.to_dict())
    else:
        sys.stdout.write(_format_plan_inspect_human(plan))
    return 0


def _cmd_plan_generate(args: argparse.Namespace) -> int:
    fmt: OutputFormat = args.format
    if not getattr(args, "config", None):
        raise CliUsageError("plan generation requires --config")
    if not getattr(args, "output", None):
        raise CliUsageError("plan generation requires --output")

    loaded = _load_canonical_and_settings(
        config_path=args.config,
        profile=getattr(args, "profile", None),
        fmt=fmt,
    )
    if isinstance(loaded, int):
        return loaded
    canonical, settings = loaded

    if not canonical.targets:
        return _emit_operational_message(_SAFE_TARGET_REQUIRED_PLAN, fmt)

    mode = _effective_mode_or_invalid(settings, fmt=fmt, canonical=canonical)
    if isinstance(mode, int):
        return mode
    try:
        mapping_config = _resolve_mapping_config_from_canonical(mode, canonical)
    except CliOperationalError as exc:
        return _emit_operational_message(str(exc), fmt)

    # Validate effective Collibra runtime before any PostgreSQL/Collibra I/O.
    try:
        validate_collibra_runtime(settings, canonical)
        build_target_context_projection(settings)
    except ConfigResolutionError as exc:
        return _emit_resolution_error(exc, fmt, canonical=canonical)
    except ValueError as exc:
        return _emit_runtime_invalid(exc, fmt, canonical=canonical)

    try:
        policy_set = load_normalized_policies(canonical)
    except PolicyError as exc:
        return _emit_policy_error(exc, fmt)

    try:
        model = _scan_model(settings)
    except Exception as exc:
        if _is_operational_error(exc):
            return _emit_operational(exc, fmt)
        raise

    snapshot = GovernanceSnapshot.from_model(model)
    violations = evaluate_policies(model, policy_set)
    report = build_policy_report(
        violations=violations,
        policy_identity=policy_identity(policy_set.to_identity_dict()),
        snapshot_identity=snapshot.content_identity(),
    )
    if not report["ok"]:
        if fmt == "json":
            _print_json(report)
        else:
            sys.stdout.write(format_policy_report_human(report))
        return 3

    try:
        desired = map_to_desired_state(model, mapping_config)
        adapter = build_collibra_adapter(settings, mapping_config)
        remote = adapter.read_remote_state(desired)
        sync_plan = build_sync_plan(desired, remote)
        remote_identity = compute_remote_state_identity_value(remote)
    except ConfigResolutionError as exc:
        return _emit_resolution_error(exc, fmt, canonical=canonical)
    except Exception as exc:
        if _is_operational_error(exc):
            return _emit_operational(exc, fmt)
        raise

    saved = build_saved_plan(
        settings=settings,
        sync_plan=sync_plan,
        config_identity=config_identity(canonical.identity_projection()),
        snapshot=snapshot,
        policy_set=policy_set,
        mapping_config=mapping_config,
        remote_state_identity_value=remote_identity,
    )
    written = write_saved_plan(saved, args.output)
    if fmt == "json":
        _print_json(saved.to_dict())
    else:
        sys.stdout.write(
            _format_plan_generate_human(
                saved,
                path=written,
                warning_count=sum(1 for item in violations if item.severity == "warning"),
            )
        )
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    fmt: OutputFormat = args.format
    apply = bool(getattr(args, "apply", False))
    confirm_live = bool(getattr(args, "confirm_live", False))

    # Syntactic / mode-independent usage (before any operational I/O).
    if confirm_live and not apply:
        raise CliUsageError("--confirm-live requires --apply")

    try:
        saved = load_saved_plan(args.plan_file)
    except PlanError as exc:
        return _emit_plan_error(exc, fmt)

    loaded = _load_canonical_and_settings(
        config_path=args.config,
        profile=getattr(args, "profile", None),
        fmt=fmt,
    )
    if isinstance(loaded, int):
        return loaded
    canonical, settings = loaded

    mode = _effective_mode_or_invalid(settings, fmt=fmt, canonical=canonical)
    if isinstance(mode, int):
        return mode
    _validate_apply_flags(mode=mode, apply=apply, confirm_live=confirm_live)

    try:
        policy_set = load_normalized_policies(canonical)
    except PolicyError as exc:
        return _emit_policy_error(exc, fmt)

    if not canonical.targets:
        return _emit_operational_message(_SAFE_TARGET_REQUIRED_PLAN, fmt)

    try:
        mapping_config = _resolve_mapping_config_from_canonical(mode, canonical)
    except CliOperationalError as exc:
        return _emit_operational_message(str(exc), fmt)

    try:
        validate_collibra_runtime(settings, canonical)
        target_projection = build_target_context_projection(settings)
    except ConfigResolutionError as exc:
        return _emit_resolution_error(exc, fmt, canonical=canonical)
    except ValueError as exc:
        return _emit_runtime_invalid(exc, fmt, canonical=canonical)

    observed_public = target_context_public(target_projection)
    observed_target = target_context_identity(target_projection)
    if (
        dict(saved.target_context) != observed_public
        and observed_target == saved.target_context_identity
    ):
        # Public inspectable context must not diverge when identities still match.
        return _emit_plan_error(
            PlanIntegrityError(
                [
                    PlanDiagnosticError(
                        code=CODE_TARGET_CONTEXT_INCONSISTENT,
                        path="/target_context",
                        message=(
                            "saved target_context is inconsistent with "
                            "target_context_identity and effective runtime"
                        ),
                    )
                ]
            ),
            fmt,
        )

    try:
        model = _scan_model(settings)
    except Exception as exc:
        if _is_operational_error(exc):
            return _emit_operational(exc, fmt)
        raise

    snapshot = GovernanceSnapshot.from_model(model)
    observed_config = config_identity(canonical.identity_projection())
    observed_policy = policy_identity(policy_set.to_identity_dict())
    observed_snapshot = snapshot.content_identity()
    observed_mapping = mapping_identity(mapping_config.to_identity_dict())

    mismatches: list[dict[str, Any]] = []
    if observed_config != saved.config_identity:
        mismatches.append(
            identity_mismatch(
                category="config",
                expected=saved.config_identity,
                observed=observed_config,
                message="governance config identity changed",
            )
        )
    if observed_snapshot != saved.snapshot_identity:
        mismatches.append(
            identity_mismatch(
                category="snapshot",
                expected=saved.snapshot_identity,
                observed=observed_snapshot,
                message="source snapshot identity changed",
            )
        )
    if observed_policy != saved.policy_identity:
        mismatches.append(
            identity_mismatch(
                category="policy",
                expected=saved.policy_identity,
                observed=observed_policy,
                message="policy identity changed",
            )
        )
    if observed_mapping != saved.mapping_identity:
        mismatches.append(
            identity_mismatch(
                category="mapping",
                expected=saved.mapping_identity,
                observed=observed_mapping,
                message="mapping identity changed",
            )
        )
    if observed_target != saved.target_context_identity:
        mismatches.append(
            identity_mismatch(
                category="target_context",
                expected=saved.target_context_identity,
                observed=observed_target,
                message="effective target context changed",
            )
        )
    if saved.planner_contract_version != PLANNER_CONTRACT_VERSION:
        mismatches.append(
            version_mismatch(
                category="planner_contract",
                expected=saved.planner_contract_version,
                observed=PLANNER_CONTRACT_VERSION,
                message="planner contract version changed",
            )
        )
    if saved.scanner_contract_version != SCANNER_CONTRACT_VERSION:
        mismatches.append(
            version_mismatch(
                category="scanner_contract",
                expected=saved.scanner_contract_version,
                observed=SCANNER_CONTRACT_VERSION,
                message="scanner contract version changed",
            )
        )

    adapter = None
    target_fresh = (
        observed_target == saved.target_context_identity
        and dict(saved.target_context) == observed_public
    )
    if target_fresh:
        try:
            desired = map_to_desired_state(model, mapping_config)
            adapter = build_collibra_adapter(settings, mapping_config)
            remote = adapter.read_remote_state(desired)
            observed_remote = compute_remote_state_identity_value(remote)
        except ConfigResolutionError as exc:
            return _emit_resolution_error(exc, fmt, canonical=canonical)
        except Exception as exc:
            if _is_operational_error(exc):
                return _emit_operational(exc, fmt)
            raise
        if observed_remote != saved.remote_state_identity:
            mismatches.append(
                identity_mismatch(
                    category="remote_state",
                    expected=saved.remote_state_identity,
                    observed=observed_remote,
                    message="remote state identity changed",
                )
            )

    if mismatches:
        stale = build_stale_result(mismatches)
        if fmt == "json":
            _print_json(stale)
        else:
            sys.stdout.write(format_stale_human(stale))
        return 5

    if adapter is None:
        # Unreachable when fresh: matching target_context always builds adapter above.
        try:
            validate_collibra_runtime(settings, canonical)
            adapter = build_collibra_adapter(settings, mapping_config)
        except ConfigResolutionError as exc:
            return _emit_resolution_error(exc, fmt, canonical=canonical)
        except Exception as exc:
            if _is_operational_error(exc):
                return _emit_operational(exc, fmt)
            raise

    try:
        result = execute_collibra_plan(
            adapter,
            saved.sync_plan,
            mapping_config,
            apply=apply,
            execution_mode=settings.collibra_execution_mode,
        )
    except CollibraAdapterError as exc:
        return _emit_operational(exc, fmt)
    if isinstance(result, ImportExecutionResult):
        payload = build_import_submission_result(
            result=result,
            plan_content_identity=saved.content_identity(),
        )
        if fmt == "json":
            _print_json(payload)
        else:
            sys.stdout.write(format_import_submission_human(payload))
        return 0 if result.error is None else 1
    payload = build_apply_result(
        sync_plan=saved.sync_plan,
        result=result,
        plan_content_identity=saved.content_identity(),
    )
    if fmt == "json":
        _print_json(payload)
    else:
        sys.stdout.write(format_apply_result_human(payload))
    return 0 if result.success else 1


def _cmd_impact(args: argparse.Namespace) -> int:
    fmt: OutputFormat = args.format
    namespace = str(args.namespace).strip()
    if not namespace:
        raise CliUsageError("--namespace must be a non-empty string")
    if getattr(args, "profile", None) is not None and getattr(args, "config", None) is None:
        raise CliUsageError("--profile requires --config")

    odcs_paths = list(getattr(args, "odcs", None) or [])
    dbt_paths = list(getattr(args, "dbt_manifest", None) or [])
    openlineage_paths = list(getattr(args, "openlineage", None) or [])
    if not odcs_paths and not dbt_paths and not openlineage_paths:
        raise CliUsageError("at least one of --odcs, --dbt-manifest, or --openlineage is required")

    try:
        changed_nodes = load_impact_changes(args.changes, expected_namespace=namespace)
    except ImpactError as exc:
        return _emit_impact_error(exc, fmt)

    try:
        graph = _compose_impact_graph(
            namespace=namespace,
            odcs_paths=odcs_paths,
            dbt_paths=dbt_paths,
            openlineage_paths=openlineage_paths,
            dbt_default_database=getattr(args, "dbt_default_database", None),
        )
    except ImpactError as exc:
        return _emit_impact_error(exc, fmt)

    try:
        impact = analyze_downstream_impact(graph, changed_nodes)
    except ValueError as exc:
        return _emit_impact_error(
            ImpactChangedNodeError(
                [
                    ImpactDiagnosticError(
                        code=CODE_CHANGED_NODE,
                        path="/changed_nodes",
                        message=str(exc) or "changed nodes are invalid for composed graph",
                    )
                ]
            ),
            fmt,
        )

    policy_set = None
    affected_policies = ()
    config_path = getattr(args, "config", None)
    if config_path is not None:
        try:
            canonical = load_canonical_config(
                config_path,
                profile=getattr(args, "profile", None),
            )
        except ConfigContractError as exc:
            return _emit_config_contract_error(exc, fmt)
        try:
            policy_set = load_normalized_policies(canonical)
        except PolicyError as exc:
            return _emit_policy_error(exc, fmt)
        affected_policies = match_affected_policies(policy_set, impact.policy_relevant_nodes)

    payload = build_impact_result(
        graph=graph,
        impact=impact,
        affected_policies=affected_policies,
        policy_set=policy_set,
    )

    try:
        write_impact_result(args.output, payload)
    except OSError:
        return _emit_operational_message("unable to write impact result artifact", fmt)

    if fmt == "json":
        _write_impact_json_stdout(canonical_impact_json(payload))
    else:
        sys.stdout.write(format_impact_result_human(payload, graph=graph, output_path=args.output))
    return 6 if payload["impact_detected"] else 0


def _write_impact_json_stdout(text: str) -> None:
    """Write exact UTF-8 machine JSON bytes to stdout (LF preserved on Windows)."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
        return
    sys.stdout.write(text)


def _compose_impact_graph(
    *,
    namespace: str,
    odcs_paths: list[str],
    dbt_paths: list[str],
    openlineage_paths: list[str],
    dbt_default_database: str | None,
) -> GovernanceGraph:
    specs: list[tuple[str, str]] = []
    specs.extend(("dbt", path) for path in dbt_paths)
    specs.extend(("odcs", path) for path in odcs_paths)
    specs.extend(("openlineage", path) for path in openlineage_paths)
    specs.sort(key=lambda item: (item[0], item[1]))

    graphs: list[GovernanceGraph] = []
    for kind, path in specs:
        try:
            if kind == "odcs":
                graphs.append(load_odcs_graph(path, namespace=namespace))
            elif kind == "dbt":
                graphs.append(
                    load_dbt_graph(
                        path,
                        namespace=namespace,
                        default_database=dbt_default_database,
                    )
                )
            else:
                graphs.append(load_openlineage_graph(path, namespace=namespace))
        except (OdcsError, DbtError, OpenLineageError) as exc:
            raise ImpactSourceError(
                [
                    ImpactDiagnosticError(
                        code=CODE_SOURCE,
                        path=getattr(item, "path", "") or "",
                        message=item.message,
                        source_kind=kind,
                    )
                    for item in exc.errors
                ]
            ) from exc

    try:
        return GovernanceGraph.from_parts(
            [node for graph in graphs for node in graph.nodes],
            [edge for graph in graphs for edge in graph.edges],
        )
    except ValueError as exc:
        raise ImpactGraphConflictError(
            [
                ImpactDiagnosticError(
                    code=CODE_GRAPH_CONFLICT,
                    path="",
                    message=str(exc) or "composed governance graph is invalid",
                )
            ]
        ) from exc


def _emit_impact_error(exc: ImpactError, fmt: OutputFormat) -> int:
    payload = impact_diagnostics_failure(exc.errors)
    if fmt == "json":
        _print_json(payload)
    else:
        print("ok=false", file=sys.stderr)
        for item in exc.errors:
            path = format_human_value(item.path or "/")
            code = format_human_value(item.code)
            message = format_human_value(item.message)
            source = ""
            if item.source_kind is not None:
                source = f" source_kind={format_human_value(item.source_kind)}"
            print(
                f"error path={path} code={code} message={message}{source}",
                file=sys.stderr,
            )
    return 4


def _load_canonical_and_settings(
    *,
    config_path: str,
    profile: str | None,
    fmt: OutputFormat,
) -> tuple[CanonicalConfig, Settings] | int:
    try:
        canonical = load_canonical_config(config_path, profile=profile)
    except ConfigContractError as exc:
        return _emit_config_contract_error(exc, fmt)
    try:
        settings = resolve_settings(canonical)
    except ConfigResolutionError as exc:
        return _emit_resolution_error(exc, fmt, canonical=canonical)
    return canonical, settings


def _emit_config_contract_error(exc: ConfigContractError, fmt: OutputFormat) -> int:
    payload = diagnostics_failure(exc.errors)
    if fmt == "json":
        _print_json(payload)
    else:
        print("ok=false", file=sys.stderr)
        for item in exc.errors:
            print(
                f"error path={item.path or '/'} code={item.code} message={item.message}",
                file=sys.stderr,
            )
    return 4


def _emit_resolution_error(
    exc: ConfigResolutionError,
    fmt: OutputFormat,
    *,
    canonical: CanonicalConfig | None,
) -> int:
    del canonical  # path/code are carried on the exception
    path = exc.path or ""
    code = exc.code or CODE_ENV_UNRESOLVED
    if code == CODE_ENV_UNRESOLVED:
        payload = unresolved_env_diagnostic(path=path, message=_SAFE_RESOLUTION)
        human = _SAFE_RESOLUTION
    else:
        message = _safe_error_message(exc)
        payload = runtime_invalid_diagnostic(path=path, message=message)
        human = message
    if fmt == "json":
        _print_json(payload)
    else:
        print(f"error: {human}", file=sys.stderr)
        if path:
            print(f"  path={path}", file=sys.stderr)
    return 4


def _emit_runtime_invalid(
    exc: BaseException,
    fmt: OutputFormat,
    *,
    canonical: CanonicalConfig | None,
) -> int:
    path = ""
    if canonical is not None and canonical.targets:
        auth = canonical.targets[0].config.auth
        if auth is not None and auth.base_url_env is not None:
            path = "/targets/0/config/auth/base_url_env"
    message = _safe_error_message(exc)
    payload = runtime_invalid_diagnostic(path=path, message=message)
    if fmt == "json":
        _print_json(payload)
    else:
        print(f"error: {message}", file=sys.stderr)
        if path:
            print(f"  path={path}", file=sys.stderr)
    return 4


def _emit_policy_error(exc: PolicyError, fmt: OutputFormat) -> int:
    payload = policy_diagnostics_failure(exc.errors)
    if fmt == "json":
        _print_json(payload)
    else:
        print("ok=false", file=sys.stderr)
        for item in exc.errors:
            source = f" source={item.source}" if item.source else ""
            print(
                f"error path={item.path or '/'} code={item.code} message={item.message}{source}",
                file=sys.stderr,
            )
    return 4


def _emit_plan_error(exc: PlanError, fmt: OutputFormat) -> int:
    payload = plan_diagnostics_failure(exc.errors)
    if fmt == "json":
        _print_json(payload)
    else:
        print("ok=false", file=sys.stderr)
        for item in exc.errors:
            print(
                f"error path={item.path or '/'} code={item.code} message={item.message}",
                file=sys.stderr,
            )
    return 4


def _emit_operational(exc: BaseException, fmt: OutputFormat) -> int:
    return _emit_operational_message(_safe_error_message(exc), fmt)


def _emit_operational_message(message: str, fmt: OutputFormat) -> int:
    if fmt == "json":
        _print_json(operation_diagnostics_failure(message))
    else:
        print(f"error: {message}", file=sys.stderr)
    return 1


def _resolve_mode(settings: Settings, cli_mode: str | None) -> Mode:
    if cli_mode is not None:
        mode = cli_mode.strip().lower()
    else:
        mode = settings.collibra_mode.strip().lower()
    if mode not in {"mock", "live"}:
        raise CliOperationalError("collibra_mode must be 'mock' or 'live'")
    return mode  # type: ignore[return-value]


def _effective_mode_or_invalid(
    settings: Settings,
    *,
    fmt: OutputFormat,
    canonical: CanonicalConfig,
) -> Mode | int:
    mode = settings.collibra_mode.strip().lower()
    if mode not in {"mock", "live"}:
        return _emit_runtime_invalid(
            ValueError("collibra_mode must be 'mock' or 'live'"),
            fmt,
            canonical=canonical,
        )
    return mode  # type: ignore[return-value]


def _validate_sync_flags(*, mode: Mode, apply: bool, confirm_live: bool) -> None:
    """Validate sync flag combinations before any operational I/O."""
    if confirm_live and not apply:
        raise CliUsageError("--confirm-live requires --apply")
    if confirm_live and mode != "live":
        raise CliUsageError("--confirm-live is only valid with --mode live")
    if mode == "live" and apply and not confirm_live:
        raise CliUsageError("live apply requires --confirm-live")


def _validate_apply_flags(*, mode: Mode, apply: bool, confirm_live: bool) -> None:
    """Validate apply flag combinations against effective mode after resolution."""
    if confirm_live and mode != "live":
        raise CliUsageError("--confirm-live is only valid when effective mode is live")
    if mode == "live" and apply and not confirm_live:
        raise CliUsageError("live apply requires --confirm-live")


def _resolve_cli_mapping_path(
    canonical: CanonicalConfig | None,
    cli_mapping: str | None,
) -> str | None:
    if cli_mapping is not None:
        return cli_mapping
    if canonical is None:
        return None
    mapped = resolve_mapping_path(canonical)
    return str(mapped) if mapped is not None else None


def _resolve_export_output(
    settings: Settings,
    *,
    canonical: CanonicalConfig | None,
    artifact: ArtifactKind,
    cli_output: str | None,
) -> str:
    if cli_output is not None:
        return cli_output
    if canonical is not None:
        if artifact == "snapshot":
            return str(resolve_snapshot_path(canonical))
        return str(resolve_inventory_path(canonical))
    if artifact == "snapshot":
        inventory = Path(settings.inventory_output_path)
        return str(inventory.with_name("governance-snapshot.json"))
    return settings.inventory_output_path


def _cmd_scan(settings: Settings, *, json_output: bool) -> int:
    model = _scan_model(settings)
    summary = _scan_summary(model)
    if json_output:
        _print_json(summary)
    else:
        _print_scan_human(summary)
    return 0


def _cmd_export(
    settings: Settings,
    *,
    output_path: str,
    artifact: ArtifactKind,
) -> int:
    model = _scan_model(settings)
    if artifact == "snapshot":
        from governance.snapshots import write_snapshot

        snapshot = GovernanceSnapshot.from_model(model)
        written = write_snapshot(snapshot, output_path)
        print(f"snapshot_written={written}")
        return 0
    inventory = MetadataInventory.from_model(model)
    written = write_inventory(inventory, output_path)
    print(f"inventory_written={written}")
    return 0


def _cmd_diff(
    settings: Settings,
    *,
    mode: Mode,
    mapping_config_path: str | None,
    json_output: bool,
) -> int:
    mapping_config = _resolve_mapping_config(mode, mapping_config_path)
    effective = replace(settings, collibra_mode=mode)
    model = _scan_model(effective)
    desired = map_to_desired_state(model, mapping_config)
    adapter = build_collibra_adapter(effective, mapping_config)
    remote = adapter.read_remote_state(desired)
    plan = build_sync_plan(desired, remote)
    payload = _diff_payload(mode=mode, plan=plan)
    if json_output:
        _print_json(payload)
    else:
        _print_diff_human(payload, plan)
    return 0


def _cmd_sync(
    settings: Settings,
    *,
    mode: Mode,
    mapping_config_path: str | None,
    apply: bool,
    json_output: bool,
) -> int:
    mapping_config = _resolve_mapping_config(mode, mapping_config_path)
    effective = replace(settings, collibra_mode=mode)
    model = _scan_model(effective)
    desired = map_to_desired_state(model, mapping_config)
    adapter = build_collibra_adapter(effective, mapping_config)
    remote = adapter.read_remote_state(desired)
    plan = build_sync_plan(desired, remote)
    try:
        result = execute_collibra_plan(
            adapter,
            plan,
            mapping_config,
            apply=apply,
            execution_mode=settings.collibra_execution_mode,
        )
    except CollibraAdapterError as exc:
        return _emit_operational(exc, "json" if json_output else "human")
    if isinstance(result, ImportExecutionResult):
        if result.error is not None:
            raise CliOperationalError(result.error)
        payload = build_import_sync_payload(mode=mode, result=result)
        if json_output:
            _print_json(payload)
        else:
            sys.stdout.write(format_import_sync_human(payload))
        return 0
    if not result.success:
        message = result.error or _SAFE_SYNC_FAILED
        raise CliOperationalError(message)
    payload = _sync_payload(mode=mode, plan=plan, result=result)
    if json_output:
        _print_json(payload)
    else:
        _print_sync_human(payload)
    return 0


def _resolve_mapping_config(
    mode: Mode,
    mapping_config_path: str | None,
) -> CollibraMappingConfig:
    if mode == "mock":
        return mock_mapping_config()
    if not mapping_config_path:
        raise CliOperationalError(_SAFE_MAPPING_REQUIRED)
    try:
        config = load_mapping_config_file(mapping_config_path)
    except CollibraMappingError as exc:
        raise CliOperationalError(_SAFE_MAPPING) from exc
    if mapping_contains_example_placeholders(config):
        raise CliOperationalError(_SAFE_PLACEHOLDER)
    return config


def _resolve_mapping_config_from_canonical(
    mode: Mode,
    canonical: CanonicalConfig,
) -> CollibraMappingConfig:
    if mode == "mock":
        return mock_mapping_config()
    mapped = resolve_mapping_path(canonical)
    if mapped is None:
        raise CliOperationalError(_SAFE_MAPPING_REQUIRED_GAC)
    try:
        config = load_mapping_config_file(str(mapped))
    except CollibraMappingError as exc:
        raise CliOperationalError(_SAFE_MAPPING) from exc
    if mapping_contains_example_placeholders(config):
        raise CliOperationalError(_SAFE_PLACEHOLDER)
    return config


def _scan_model(settings: Settings) -> GovernanceModel:
    return PostgresMetadataScanner(settings).scan()


def _scan_summary(model: GovernanceModel) -> dict[str, Any]:
    if len(model.data_sources) != 1:
        raise CliOperationalError("governance model must contain exactly one data source")
    data_source = model.data_sources[0]
    if len(data_source.databases) != 1:
        raise CliOperationalError("data source must contain exactly one database")
    database = data_source.databases[0]
    schemas = database.schemas
    tables = tuple(table for schema in schemas for table in schema.tables)
    columns = tuple(column for table in tables for column in table.columns)
    primary_keys = sum(1 for table in tables if table.primary_key is not None)
    foreign_keys = sum(len(table.foreign_keys) for table in tables)
    return {
        "source": data_source.name,
        "database": database.name,
        "schemas": len(schemas),
        "tables": len(tables),
        "columns": len(columns),
        "primary_keys": primary_keys,
        "foreign_keys": foreign_keys,
        "relationships": len(model.relationships),
    }


def _diff_payload(*, mode: Mode, plan: SyncPlan) -> dict[str, Any]:
    actions = [
        _action_dict(action)
        for action in plan.actions
        if action.action_type is not SyncActionType.UNCHANGED
    ]
    return {
        "mode": mode,
        "create": len(plan.creates),
        "update": len(plan.updates),
        "unchanged": len(plan.unchanged),
        "remote_only": len(plan.remote_only),
        "writes": 0,
        "actions": actions,
    }


def _sync_payload(*, mode: Mode, plan: SyncPlan, result: SyncResult) -> dict[str, Any]:
    return {
        "mode": mode,
        "dry_run": result.dry_run,
        "create": len(plan.creates),
        "update": len(plan.updates),
        "unchanged": len(plan.unchanged),
        "remote_only": len(plan.remote_only),
        "applied": result.applied_count,
        "success": result.success,
    }


def _action_dict(action: SyncAction) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action_type": action.action_type.value,
        "object_kind": action.object_kind.value,
        "local_id": action.local_id,
    }
    if action.changed_fields:
        payload["changed_fields"] = list(action.changed_fields)
    return payload


def _format_plan_generate_human(
    plan: SavedGovernancePlan,
    *,
    path: Path,
    warning_count: int,
) -> str:
    sync = plan.sync_plan
    lines = [
        f"plan_written={path}",
        f"create={len(sync.creates)}",
        f"update={len(sync.updates)}",
        f"unchanged={len(sync.unchanged)}",
        f"remote_only={len(sync.remote_only)}",
        f"warnings={warning_count}",
        f"config_identity={plan.config_identity.digest}",
        f"snapshot_identity={plan.snapshot_identity.digest}",
        f"policy_identity={plan.policy_identity.digest}",
        f"mapping_identity={plan.mapping_identity.digest}",
        f"target_context_identity={plan.target_context_identity.digest}",
        f"remote_state_identity={plan.remote_state_identity.digest}",
        f"content_identity={plan.content_identity().digest}",
    ]
    return "\n".join(lines) + "\n"


def _format_plan_inspect_human(plan: SavedGovernancePlan) -> str:
    sync = plan.sync_plan
    lines = [
        f"plan_schema={plan.plan_schema}",
        f"plan_version={plan.plan_version}",
        f"planner_contract_version={plan.planner_contract_version}",
        f"scanner_contract_version={plan.scanner_contract_version}",
        f"target_context provider={plan.target_context.get('provider')} "
        f"mode={plan.target_context.get('mode')}",
        f"create={len(sync.creates)}",
        f"update={len(sync.updates)}",
        f"unchanged={len(sync.unchanged)}",
        f"remote_only={len(sync.remote_only)}",
        f"config_identity={plan.config_identity.digest}",
        f"snapshot_identity={plan.snapshot_identity.digest}",
        f"policy_identity={plan.policy_identity.digest}",
        f"mapping_identity={plan.mapping_identity.digest}",
        f"target_context_identity={plan.target_context_identity.digest}",
        f"remote_state_identity={plan.remote_state_identity.digest}",
        f"content_identity={plan.content_identity().digest}",
    ]
    for action in sync.actions:
        if action.action_type is SyncActionType.UNCHANGED:
            continue
        lines.append(_format_action_line(action))
    return "\n".join(lines) + "\n"


def _print_scan_human(summary: dict[str, Any]) -> None:
    print(f"source={summary['source']}")
    print(f"database={summary['database']}")
    print(f"schemas={summary['schemas']}")
    print(f"tables={summary['tables']}")
    print(f"columns={summary['columns']}")
    print(f"primary_keys={summary['primary_keys']}")
    print(f"foreign_keys={summary['foreign_keys']}")
    print(f"relationships={summary['relationships']}")


def _print_diff_human(payload: dict[str, Any], plan: SyncPlan) -> None:
    print(f"mode={payload['mode']}")
    print(f"create={payload['create']}")
    print(f"update={payload['update']}")
    print(f"unchanged={payload['unchanged']}")
    print(f"remote_only={payload['remote_only']}")
    print(f"writes={payload['writes']}")
    for action in plan.actions:
        if action.action_type is SyncActionType.UNCHANGED:
            continue
        print(_format_action_line(action))


def _print_sync_human(payload: dict[str, Any]) -> None:
    print(f"mode={payload['mode']}")
    print(f"dry_run={str(payload['dry_run']).lower()}")
    print(f"create={payload['create']}")
    print(f"update={payload['update']}")
    print(f"unchanged={payload['unchanged']}")
    print(f"remote_only={payload['remote_only']}")
    print(f"applied={payload['applied']}")
    print(f"success={str(payload['success']).lower()}")


def _format_action_line(action: SyncAction) -> str:
    label = action.action_type.value.upper()
    kind = _object_label(action)
    local_id = action.local_id or ""
    line = f"{label} {kind} {local_id}".rstrip()
    if action.action_type is SyncActionType.UPDATE and action.changed_fields:
        fields = ",".join(action.changed_fields)
        line = f"{line} fields={fields}"
    return line


def _object_label(action: SyncAction) -> str:
    if action.object_kind is SyncObjectKind.RELATIONSHIP:
        return "relationship"
    local_id = action.local_id or ""
    if local_id.startswith("db:"):
        return "database"
    if local_id.startswith("sch:"):
        return "schema"
    if local_id.startswith("tbl:"):
        return "table"
    if local_id.startswith("col:"):
        return "column"
    return "asset"


def _print_json(payload: dict[str, Any], stream: TextIO | None = None) -> None:
    target = sys.stdout if stream is None else stream
    target.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    target.write("\n")


def _safe_error_message(exc: BaseException) -> str:
    if isinstance(exc, CollibraMappingError):
        return _SAFE_MAPPING
    text = str(exc).strip()
    if not text:
        return _SAFE_UNEXPECTED
    lowered = text.lower()
    for secret_marker in (
        "password",
        "bearer",
        "authorization",
        "token=",
        "client_secret",
        "access_token",
    ):
        if secret_marker in lowered:
            return _SAFE_UNEXPECTED
    return text


if __name__ == "__main__":
    raise SystemExit(main())

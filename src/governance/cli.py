"""Governance CLI: scan, export, diff, sync, and check orchestration."""

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
    unresolved_env_diagnostic,
    validate_governance_config,
)
from governance.domain import GovernanceModel
from governance.exporters import (
    InventoryExportError,
    MetadataInventory,
    write_inventory,
)
from governance.identity import (
    policy_identity,
)
from governance.integrations.collibra import (
    CollibraAdapterError,
    CollibraMappingConfig,
    CollibraMappingError,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    SyncResult,
    build_collibra_adapter,
    build_sync_plan,
    execute_sync_plan,
    load_mapping_config_file,
    map_to_desired_state,
    mapping_contains_example_placeholders,
    mock_mapping_config,
)
from governance.operation_diagnostics import operation_diagnostics_failure
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
_SAFE_SYNC_FAILED = "synchronization failed"
_SAFE_TARGET_REQUIRED = "governance.yaml must define a target for diff/sync"
_SAFE_CONFIG = "invalid governance configuration"
_SAFE_RESOLUTION = "required environment reference could not be resolved"

_OPERATIONAL_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ValueError,
    MetadataDiscoveryError,
    InventoryExportError,
    CollibraMappingError,
    CollibraAdapterError,
    ConfigResolutionError,
)


class CliUsageError(Exception):
    """CLI flag combination error (exit code 2, no operational I/O)."""


class CliOperationalError(Exception):
    """Expected operational failure translated to a safe stderr message."""


def _is_operational_error(exc: BaseException) -> bool:
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
            "policies, and run Collibra diff/sync. Dry-run means "
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
    path = ""
    if canonical is not None and canonical.sources:
        connection = canonical.sources[0].config.connection
        if connection.database_url_env and connection.database_url_env in str(exc):
            path = "/sources/0/config/connection/database_url_env"
    payload = unresolved_env_diagnostic(path=path, message=_SAFE_RESOLUTION)
    if fmt == "json":
        _print_json(payload)
    else:
        print(f"error: {_SAFE_RESOLUTION}", file=sys.stderr)
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



def _validate_sync_flags(*, mode: Mode, apply: bool, confirm_live: bool) -> None:
    """Validate sync flag combinations before any operational I/O."""
    if confirm_live and not apply:
        raise CliUsageError("--confirm-live requires --apply")
    if confirm_live and mode != "live":
        raise CliUsageError("--confirm-live is only valid with --mode live")
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
    result = execute_sync_plan(adapter, plan, apply=apply)
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
    for secret_marker in ("password", "bearer", "authorization", "token="):
        if secret_marker in lowered:
            return _SAFE_UNEXPECTED
    return text


if __name__ == "__main__":
    raise SystemExit(main())

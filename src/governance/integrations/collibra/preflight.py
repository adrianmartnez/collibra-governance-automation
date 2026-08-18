"""Read-only Collibra tenant compatibility preflight.

Transport is classified before any adapter is constructed so HTTP non-loopback
never sends Authorization headers or token bodies. Loopback HTTP is a test-only
exception and is never presented as production HTTPS. Write capability is never
reported as verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from governance.config import Settings
from governance.integrations.collibra.adapters import CollibraAdapterError, CollibraAuthError
from governance.integrations.collibra.endpoint import classify_transport
from governance.integrations.collibra.live import API_PREFIX, LiveCollibraAdapter
from governance.integrations.collibra.mapping import CollibraMappingConfig

STATUS_VERIFIED = "VERIFIED"
STATUS_WARNING = "WARNING"
STATUS_NOT_VERIFIED = "NOT_VERIFIED"
STATUS_INCOMPATIBLE = "INCOMPATIBLE"
STATUS_OPERATIONAL_FAILURE = "OPERATIONAL_FAILURE"

_RANK = {
    STATUS_INCOMPATIBLE: 0,
    STATUS_OPERATIONAL_FAILURE: 1,
    STATUS_NOT_VERIFIED: 2,
    STATUS_WARNING: 3,
    STATUS_VERIFIED: 4,
}

CODE_MALFORMED_URL = "malformed_url"
CODE_EMBEDDED_CREDENTIALS = "embedded_credentials"
CODE_REMOTE_HTTP = "remote_http_rejected"
CODE_LOOPBACK_HTTP = "loopback_http_test_exception"
CODE_HTTPS = "https_transport"
CODE_MOCK_MODE = "mock_mode_not_verified"
CODE_APPLICATION_INFO = "application_info"
CODE_AUTH_FAILURE = "authentication_failed"
CODE_MAPPING_REF = "mapping_ref"
CODE_MAPPING_MISSING = "mapping_ref_missing"
CODE_WRITES_NOT_PROBED = "write_capability_not_probed"
CODE_OPERATIONAL = "operational_failure"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """One inspectable preflight finding. Never includes secrets."""

    id: str
    status: str
    code: str
    message: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocking": self.blocking,
            "code": self.code,
            "id": self.id,
            "message": self.message,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Deterministic preflight result. ``writes_performed`` is always 0."""

    overall: str
    mode: str
    transport: str | None
    writes_performed: int
    checks: tuple[PreflightCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [check.to_dict() for check in self.checks],
            "mode": self.mode,
            "overall": self.overall,
            "transport": self.transport,
            "writes_performed": self.writes_performed,
        }


def format_preflight_human(report: PreflightReport) -> str:
    lines = [
        f"overall={report.overall}",
        f"writes_performed={report.writes_performed}",
        f"mode={report.mode}",
        f"transport={report.transport or 'none'}",
    ]
    for check in report.checks:
        lines.append(
            f"check {check.id} status={check.status} code={check.code} "
            f"blocking={str(check.blocking).lower()}"
        )
        lines.append(f"  {check.message}")
    return "\n".join(lines) + "\n"


def preflight_exit_code(report: PreflightReport) -> int:
    if report.overall in {STATUS_INCOMPATIBLE, STATUS_OPERATIONAL_FAILURE}:
        return 1
    return 0


def run_preflight(
    settings: Settings,
    mapping_config: CollibraMappingConfig,
    *,
    transport: httpx.BaseTransport | None = None,
) -> PreflightReport:
    """Classify transport before credentials, then issue read-only GETs."""
    mode = settings.collibra_mode.strip().lower()
    if mode == "mock":
        return _report(
            overall=STATUS_NOT_VERIFIED,
            mode="mock",
            transport=None,
            checks=(
                PreflightCheck(
                    id="mode",
                    status=STATUS_NOT_VERIFIED,
                    code=CODE_MOCK_MODE,
                    message="mock mode does not contact a Collibra tenant",
                ),
                _writes_check(),
            ),
        )

    endpoint = _classify_endpoint(settings.collibra_base_url, field="collibra_base_url")
    if isinstance(endpoint, PreflightReport):
        return endpoint
    transport_class, transport_check = endpoint
    checks: list[PreflightCheck] = [transport_check]

    token_gate = _classify_optional_token_url(settings.collibra_token_url)
    if token_gate is not None:
        return token_gate

    adapter = LiveCollibraAdapter.from_settings(
        settings,
        mapping_config,
        transport=transport,
    )
    try:
        auth_check = _probe_application_info(adapter)
        checks.append(auth_check)
        if auth_check.status != STATUS_VERIFIED:
            return _report(
                overall=_rollup(checks),
                mode="live",
                transport=transport_class,
                checks=(*checks, _writes_check()),
            )
        checks.extend(_probe_mapping_refs(adapter, mapping_config))
    except CollibraAuthError:
        checks.append(
            PreflightCheck(
                id="auth",
                status=STATUS_INCOMPATIBLE,
                code=CODE_AUTH_FAILURE,
                message="authentication failed on a read-only request",
            )
        )
    except CollibraAdapterError as exc:
        if exc.status_code == 401:
            checks.append(
                PreflightCheck(
                    id="auth",
                    status=STATUS_INCOMPATIBLE,
                    code=CODE_AUTH_FAILURE,
                    message="authentication failed on a read-only request",
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    id="operational",
                    status=STATUS_OPERATIONAL_FAILURE,
                    code=CODE_OPERATIONAL,
                    message=_safe_operational_message(exc),
                )
            )
    except httpx.HTTPError:
        checks.append(
            PreflightCheck(
                id="operational",
                status=STATUS_OPERATIONAL_FAILURE,
                code=CODE_OPERATIONAL,
                message="Collibra HTTP transport failed",
            )
        )
    finally:
        adapter.close()

    checks.append(_writes_check())
    return _report(
        overall=_rollup(checks),
        mode="live",
        transport=transport_class,
        checks=tuple(checks),
    )


def _classify_endpoint(
    raw_url: str,
    *,
    field: str,
) -> tuple[str, PreflightCheck] | PreflightReport:
    raw = (raw_url or "").strip()
    if not raw:
        return _incompatible(
            id="endpoint",
            code=CODE_MALFORMED_URL,
            message=f"{field} is required for live preflight",
        )
    parsed = urlparse(raw)
    if parsed.username or parsed.password:
        return _incompatible(
            id="endpoint",
            code=CODE_EMBEDDED_CREDENTIALS,
            message=f"{field} must not embed credentials",
        )
    try:
        transport_class = classify_transport(raw)
    except ValueError:
        return _incompatible(
            id="endpoint",
            code=CODE_MALFORMED_URL,
            message=f"{field} must be an absolute http(s) URL",
        )
    if transport_class == "remote_http":
        return _incompatible(
            id="transport",
            code=CODE_REMOTE_HTTP,
            message="HTTP non-loopback is incompatible; credentials were not sent",
            transport="remote_http",
        )
    if transport_class == "loopback_http":
        return (
            transport_class,
            PreflightCheck(
                id="transport",
                status=STATUS_WARNING,
                code=CODE_LOOPBACK_HTTP,
                message="HTTP loopback is a contract-test exception, not production HTTPS",
                blocking=False,
            ),
        )
    return (
        transport_class,
        PreflightCheck(
            id="transport",
            status=STATUS_VERIFIED,
            code=CODE_HTTPS,
            message="HTTPS transport is accepted for live preflight",
        ),
    )


def _classify_optional_token_url(token_url: str) -> PreflightReport | None:
    raw = (token_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.username or parsed.password:
        return _incompatible(
            id="token_url",
            code=CODE_EMBEDDED_CREDENTIALS,
            message="oauth token_url must not embed credentials",
        )
    if "client_id=" in parsed.query.lower() or "client_secret=" in parsed.query.lower():
        return _incompatible(
            id="token_url",
            code=CODE_EMBEDDED_CREDENTIALS,
            message="oauth token_url must not embed credentials",
        )
    try:
        transport_class = classify_transport(raw)
    except ValueError:
        return _incompatible(
            id="token_url",
            code=CODE_MALFORMED_URL,
            message="oauth token_url must be an absolute http(s) URL",
        )
    if transport_class == "remote_http":
        return _incompatible(
            id="token_url",
            code=CODE_REMOTE_HTTP,
            message="HTTP non-loopback token_url is incompatible; credentials were not sent",
            transport="remote_http",
        )
    return None


def _probe_application_info(adapter: LiveCollibraAdapter) -> PreflightCheck:
    payload = adapter.read_json(f"{API_PREFIX}/application/info")
    if not isinstance(payload, dict):
        return PreflightCheck(
            id="auth",
            status=STATUS_OPERATIONAL_FAILURE,
            code=CODE_OPERATIONAL,
            message="application info response is not an object",
        )
    return PreflightCheck(
        id="auth",
        status=STATUS_VERIFIED,
        code=CODE_APPLICATION_INFO,
        message="read-only authentication succeeded",
    )


def _probe_mapping_refs(
    adapter: LiveCollibraAdapter,
    mapping_config: CollibraMappingConfig,
) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    targets: list[tuple[str, str, str]] = [
        ("mapping.domain", "domains", mapping_config.domain_ref),
    ]
    for key in sorted(mapping_config.asset_type_refs):
        targets.append((f"mapping.asset.{key}", "assetTypes", mapping_config.asset_type_refs[key]))
    for key in sorted(mapping_config.attribute_type_refs):
        targets.append(
            (f"mapping.attribute.{key}", "attributeTypes", mapping_config.attribute_type_refs[key])
        )
    for key in sorted(mapping_config.relation_type_refs):
        targets.append(
            (f"mapping.relation.{key}", "relationTypes", mapping_config.relation_type_refs[key])
        )
    for check_id, family, ref in targets:
        path = f"{API_PREFIX}/{family}/{ref}"
        try:
            adapter.read_json(path)
        except CollibraAdapterError as exc:
            if exc.status_code == 404:
                checks.append(
                    PreflightCheck(
                        id=check_id,
                        status=STATUS_INCOMPATIBLE,
                        code=CODE_MAPPING_MISSING,
                        message=f"configured {check_id} was not found",
                    )
                )
                continue
            raise
        checks.append(
            PreflightCheck(
                id=check_id,
                status=STATUS_VERIFIED,
                code=CODE_MAPPING_REF,
                message=f"configured {check_id} is readable",
            )
        )
    return checks


def _writes_check() -> PreflightCheck:
    return PreflightCheck(
        id="writes",
        status=STATUS_NOT_VERIFIED,
        code=CODE_WRITES_NOT_PROBED,
        message="write and Import mutation capability are not probed",
        blocking=False,
    )


def _report(
    *,
    overall: str,
    mode: str,
    transport: str | None,
    checks: tuple[PreflightCheck, ...] | list[PreflightCheck],
) -> PreflightReport:
    ordered = tuple(sorted(tuple(checks), key=lambda item: item.id))
    return PreflightReport(
        overall=overall,
        mode=mode,
        transport=transport,
        writes_performed=0,
        checks=ordered,
    )


def _rollup(checks: list[PreflightCheck] | tuple[PreflightCheck, ...]) -> str:
    blocking = [check for check in checks if check.blocking]
    if not blocking:
        return STATUS_VERIFIED
    return min(blocking, key=lambda check: _RANK[check.status]).status


def _incompatible(
    *,
    id: str,
    code: str,
    message: str,
    transport: str | None = None,
) -> PreflightReport:
    return _report(
        overall=STATUS_INCOMPATIBLE,
        mode="live",
        transport=transport,
        checks=(
            PreflightCheck(id=id, status=STATUS_INCOMPATIBLE, code=code, message=message),
            _writes_check(),
        ),
    )


def _safe_operational_message(exc: CollibraAdapterError) -> str:
    del exc
    return "Collibra read failed"

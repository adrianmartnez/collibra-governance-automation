"""Built-in Collibra target provider."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from governance import __version__ as package_version
from governance.config import (
    DEFAULT_COLLIBRA_BATCH_MAX_ADDITIONAL_CHARACTERISTICS,
    DEFAULT_COLLIBRA_BATCH_MAX_RESOURCES,
    DEFAULT_COLLIBRA_EXECUTION_MODE,
    DEFAULT_COLLIBRA_JOB_POLL_INTERVAL_SECONDS,
    DEFAULT_COLLIBRA_JOB_POLL_TIMEOUT_SECONDS,
    DEFAULT_COLLIBRA_MODE,
    DEFAULT_COLLIBRA_TIMEOUT_SECONDS,
    DEFAULT_INVENTORY_OUTPUT_PATH,
    DEFAULT_POSTGRES_DB,
    DEFAULT_POSTGRES_HOST,
    DEFAULT_POSTGRES_PASSWORD,
    DEFAULT_POSTGRES_PORT,
    DEFAULT_POSTGRES_SOURCE_NAME,
    DEFAULT_POSTGRES_USER,
    Settings,
)
from governance.integrations.collibra.adapters import CollibraAdapter, build_collibra_adapter
from governance.integrations.collibra.import_api import execute_collibra_plan
from governance.integrations.collibra.mapping import (
    ASSET_TYPE_KEYS,
    ATTRIBUTE_TYPE_KEYS,
    RELATION_TYPE_KEYS,
    CollibraMappingConfig,
    CollibraMappingError,
)
from governance.integrations.collibra.models import (
    CollibraDesiredState,
    CollibraRemoteState,
    SyncPlan,
)
from governance.integrations.collibra.preflight import run_preflight
from governance.integrations.collibra.sync import build_sync_plan
from governance.providers.builtins._common import is_env_ref
from governance.providers.capabilities import CapabilityId
from governance.providers.contracts import (
    CapabilityBinding,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRuntimeContext,
)
from governance.providers.errors import CODE_INVALID_DESCRIPTOR, ProviderDiagnostic, ProviderError

_SECRET_KEYS = frozenset({"password", "bearer_token", "client_secret"})

_OPTIONAL_STRING_KEYS = (
    "base_url",
    "username",
    "client_id",
    "token_url",
    "oauth_scope",
    "oauth_client_auth",
    "execution_mode",
    "synchronization_id",
    "source_name",
)

_OPTIONAL_FLOAT_KEYS = (
    "timeout_seconds",
    "job_poll_interval_seconds",
    "job_poll_timeout_seconds",
)

_OPTIONAL_INT_KEYS = (
    "batch_max_resources",
    "batch_max_additional_characteristics",
)


@dataclass(frozen=True, slots=True)
class CollibraMutationRequest:
    """Authorized mutation payload (adapter supplied by the provider runtime)."""

    plan: SyncPlan
    mapping_config: CollibraMappingConfig
    apply: bool
    execution_mode: str
    synchronization_id: str | None = None
    max_resources: int | None = None
    max_additional_characteristics: int | None = None


class _CollibraProviderRuntime:
    """Resolved Collibra target configuration without operational I/O."""

    __slots__ = (
        "_adapter",
        "base_url",
        "batch_max_additional_characteristics",
        "batch_max_resources",
        "bearer_token",
        "client_id",
        "client_secret",
        "execution_mode",
        "job_poll_interval_seconds",
        "job_poll_timeout_seconds",
        "mapping_config",
        "mode",
        "oauth_client_auth",
        "oauth_scope",
        "password",
        "source_name",
        "synchronization_id",
        "timeout_seconds",
        "token_url",
        "username",
    )

    def __init__(
        self,
        *,
        mapping_config: CollibraMappingConfig,
        mode: str,
        base_url: str = "",
        username: str = "",
        password: str = "",
        bearer_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        token_url: str = "",
        oauth_scope: str = "",
        oauth_client_auth: str = "",
        timeout_seconds: float = DEFAULT_COLLIBRA_TIMEOUT_SECONDS,
        job_poll_interval_seconds: float = DEFAULT_COLLIBRA_JOB_POLL_INTERVAL_SECONDS,
        job_poll_timeout_seconds: float = DEFAULT_COLLIBRA_JOB_POLL_TIMEOUT_SECONDS,
        execution_mode: str = DEFAULT_COLLIBRA_EXECUTION_MODE,
        synchronization_id: str = "",
        batch_max_resources: int = DEFAULT_COLLIBRA_BATCH_MAX_RESOURCES,
        batch_max_additional_characteristics: int = (
            DEFAULT_COLLIBRA_BATCH_MAX_ADDITIONAL_CHARACTERISTICS
        ),
        source_name: str = DEFAULT_POSTGRES_SOURCE_NAME,
    ) -> None:
        self.mapping_config = mapping_config
        self.mode = mode
        self.base_url = base_url
        self.username = username
        self.password = password
        self.bearer_token = bearer_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url
        self.oauth_scope = oauth_scope
        self.oauth_client_auth = oauth_client_auth
        self.timeout_seconds = timeout_seconds
        self.job_poll_interval_seconds = job_poll_interval_seconds
        self.job_poll_timeout_seconds = job_poll_timeout_seconds
        self.execution_mode = execution_mode
        self.synchronization_id = synchronization_id
        self.batch_max_resources = batch_max_resources
        self.batch_max_additional_characteristics = batch_max_additional_characteristics
        self.source_name = source_name
        self._adapter: CollibraAdapter | None = None

    def build_settings(self) -> Settings:
        """Construct ``Settings`` with collibra fields from resolved provider config."""
        return Settings(
            postgres_host=DEFAULT_POSTGRES_HOST,
            postgres_port=DEFAULT_POSTGRES_PORT,
            postgres_db=DEFAULT_POSTGRES_DB,
            postgres_user=DEFAULT_POSTGRES_USER,
            postgres_password=DEFAULT_POSTGRES_PASSWORD,
            postgres_source_name=self.source_name,
            inventory_output_path=DEFAULT_INVENTORY_OUTPUT_PATH,
            collibra_mode=self.mode,
            collibra_base_url=self.base_url,
            collibra_username=self.username,
            collibra_password=self.password,
            collibra_bearer_token=self.bearer_token,
            collibra_client_id=self.client_id,
            collibra_client_secret=self.client_secret,
            collibra_token_url=self.token_url,
            collibra_oauth_scope=self.oauth_scope,
            collibra_oauth_client_auth=self.oauth_client_auth,
            collibra_timeout_seconds=self.timeout_seconds,
            collibra_job_poll_interval_seconds=self.job_poll_interval_seconds,
            collibra_job_poll_timeout_seconds=self.job_poll_timeout_seconds,
            collibra_execution_mode=self.execution_mode,
            collibra_synchronization_id=self.synchronization_id,
            collibra_batch_max_resources=self.batch_max_resources,
            collibra_batch_max_additional_characteristics=self.batch_max_additional_characteristics,
        )

    def adapter(self) -> CollibraAdapter:
        if self._adapter is None:
            self._adapter = build_collibra_adapter(self.build_settings(), self.mapping_config)
        return self._adapter

    def build_target_context(self, *, source_name: str | None = None) -> dict[str, Any]:
        from governance.integrations.collibra.synchronization import effective_synchronization_id

        settings = self.build_settings()
        if source_name is not None:
            settings = replace(settings, postgres_source_name=source_name)
        execution = settings.collibra_execution_mode
        sync_id = ""
        if execution == "sync_v2":
            sync_id = effective_synchronization_id(settings)
        from governance.plans.target_context import build_collibra_target_context_projection

        return build_collibra_target_context_projection(
            mode=settings.collibra_mode,
            base_url=settings.collibra_base_url,
            execution_mode=execution,
            synchronization_id=sync_id,
            source_name=settings.postgres_source_name,
        )


class _CollibraRemoteStateReadCapability:
    def __init__(self, runtime: _CollibraProviderRuntime) -> None:
        self._runtime = runtime

    def read_remote_state(self, request: CollibraDesiredState) -> CollibraRemoteState:
        return self._runtime.adapter().read_remote_state(request)


class _CollibraTargetPlanningCapability:
    def __init__(self, runtime: _CollibraProviderRuntime) -> None:
        _ = runtime

    def build_plan(
        self,
        desired_state: CollibraDesiredState,
        remote_state: CollibraRemoteState,
    ) -> SyncPlan:
        return build_sync_plan(desired_state, remote_state)


class _CollibraCompatibilityPreflightCapability:
    def __init__(self, runtime: _CollibraProviderRuntime) -> None:
        self._runtime = runtime

    def run_preflight(self) -> Any:
        return run_preflight(self._runtime.build_settings(), self._runtime.mapping_config)


class _CollibraAuthorizedMutationCapability:
    def __init__(self, runtime: _CollibraProviderRuntime) -> None:
        self._runtime = runtime

    def execute_authorized(self, request: CollibraMutationRequest) -> Any:
        return execute_collibra_plan(
            self._runtime.adapter(),
            request.plan,
            request.mapping_config,
            apply=request.apply,
            execution_mode=request.execution_mode,
            synchronization_id=request.synchronization_id,
            max_resources=request.max_resources,
            max_additional_characteristics=request.max_additional_characteristics,
        )


class _CollibraConfigValidator:
    def validate(self, config: Mapping[str, object]) -> None:
        diagnostics: list[ProviderDiagnostic] = []

        if "mapping_path" in config:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/mapping_path",
                    message="mapping_path is not supported; use inline mapping",
                )
            )

        mode = config.get("mode")
        if mode is None:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/mode",
                    message="mode is required",
                )
            )
        elif not (isinstance(mode, str) and mode.strip()) and not is_env_ref(mode):
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/mode",
                    message="mode must be a non-empty string or environment reference",
                )
            )

        mapping = config.get("mapping")
        if mapping is None:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/mapping",
                    message="mapping is required",
                )
            )
        elif not isinstance(mapping, Mapping):
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/mapping",
                    message="mapping must be an object",
                )
            )
        else:
            diagnostics.extend(_validate_mapping_unresolved(mapping))

        for key in _SECRET_KEYS:
            if key not in config:
                continue
            value = config[key]
            if value is None:
                continue
            if not is_env_ref(value):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path=f"/{key}",
                        message=f"{key} must be an environment reference when present",
                    )
                )

        for key in _OPTIONAL_STRING_KEYS:
            if key not in config:
                continue
            value = config[key]
            if value is None:
                continue
            if not ((isinstance(value, str) and value.strip()) or is_env_ref(value)):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path=f"/{key}",
                        message=f"{key} must be a non-empty string or environment reference",
                    )
                )

        for key in _OPTIONAL_FLOAT_KEYS:
            if key not in config:
                continue
            value = config[key]
            if value is None:
                continue
            if is_env_ref(value):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path=f"/{key}",
                        message=f"{key} must be a number or environment reference",
                    )
                )

        for key in _OPTIONAL_INT_KEYS:
            if key not in config:
                continue
            value = config[key]
            if value is None:
                continue
            if is_env_ref(value):
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path=f"/{key}",
                        message=f"{key} must be an integer or environment reference",
                    )
                )

        if diagnostics:
            raise ProviderError(diagnostics)


def _validate_mapping_unresolved(mapping: Mapping[str, object]) -> list[ProviderDiagnostic]:
    diagnostics: list[ProviderDiagnostic] = []

    domain_ref = mapping.get("domain_ref")
    if is_env_ref(domain_ref):
        diagnostics.append(
            ProviderDiagnostic(
                code=CODE_INVALID_DESCRIPTOR,
                path="/mapping/domain_ref",
                message="domain_ref must be a literal string, not an environment reference",
            )
        )
    elif not isinstance(domain_ref, str) or not domain_ref.strip():
        diagnostics.append(
            ProviderDiagnostic(
                code=CODE_INVALID_DESCRIPTOR,
                path="/mapping/domain_ref",
                message="domain_ref must be a non-empty string",
            )
        )

    for field_name, required_keys in (
        ("asset_type_refs", ASSET_TYPE_KEYS),
        ("relation_type_refs", RELATION_TYPE_KEYS),
        ("attribute_type_refs", ATTRIBUTE_TYPE_KEYS),
    ):
        refs = mapping.get(field_name)
        pointer = f"/mapping/{field_name}"
        if not isinstance(refs, Mapping):
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=pointer,
                    message=f"{field_name} must be an object",
                )
            )
            continue
        for key in required_keys:
            item_path = f"{pointer}/{key}"
            value = refs.get(key)
            if is_env_ref(value):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path=item_path,
                        message=(
                            f"{field_name}[{key!r}] must be a literal string,"
                            " not an environment reference"
                        ),
                    )
                )
            elif not isinstance(value, str) or not value.strip():
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path=item_path,
                        message=f"{field_name}[{key!r}] must be a non-empty string",
                    )
                )

    return diagnostics


def _mapping_config_from_resolved(mapping: Mapping[str, object]) -> CollibraMappingConfig:
    try:
        return CollibraMappingConfig(
            domain_ref=str(mapping["domain_ref"]),
            asset_type_refs={
                key: str(mapping["asset_type_refs"][key])  # type: ignore[index]
                for key in ASSET_TYPE_KEYS
            },
            relation_type_refs={
                key: str(mapping["relation_type_refs"][key])  # type: ignore[index]
                for key in RELATION_TYPE_KEYS
            },
            attribute_type_refs={
                key: str(mapping["attribute_type_refs"][key])  # type: ignore[index]
                for key in ATTRIBUTE_TYPE_KEYS
            },
        )
    except (CollibraMappingError, KeyError, TypeError) as exc:
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/mapping",
                    message=str(exc),
                )
            ]
        ) from exc


def _resolved_str(config: Mapping[str, object], key: str, default: str = "") -> str:
    raw = config.get(key, default)
    if raw is None:
        return default
    return str(raw)


def _resolved_float(config: Mapping[str, object], key: str, default: float) -> float:
    raw = config.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=f"/{key}",
                    message=f"{key} must be a number",
                )
            ]
        )
    return float(raw)


def _resolved_int(config: Mapping[str, object], key: str, default: int) -> int:
    raw = config.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=f"/{key}",
                    message=f"{key} must be an integer",
                )
            ]
        )
    return raw


def _runtime_from_context(context: ProviderRuntimeContext) -> _CollibraProviderRuntime:
    config = context.config
    mapping_raw = config.get("mapping")
    if not isinstance(mapping_raw, Mapping):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/mapping",
                    message="mapping must be an object",
                )
            ]
        )
    mapping_config = _mapping_config_from_resolved(mapping_raw)
    mode = _resolved_str(config, "mode", DEFAULT_COLLIBRA_MODE)
    try:
        return _CollibraProviderRuntime(
            mapping_config=mapping_config,
            mode=mode,
            base_url=_resolved_str(config, "base_url"),
            username=_resolved_str(config, "username"),
            password=_resolved_str(config, "password"),
            bearer_token=_resolved_str(config, "bearer_token"),
            client_id=_resolved_str(config, "client_id"),
            client_secret=_resolved_str(config, "client_secret"),
            token_url=_resolved_str(config, "token_url"),
            oauth_scope=_resolved_str(config, "oauth_scope"),
            oauth_client_auth=_resolved_str(config, "oauth_client_auth"),
            timeout_seconds=_resolved_float(
                config, "timeout_seconds", DEFAULT_COLLIBRA_TIMEOUT_SECONDS
            ),
            job_poll_interval_seconds=_resolved_float(
                config,
                "job_poll_interval_seconds",
                DEFAULT_COLLIBRA_JOB_POLL_INTERVAL_SECONDS,
            ),
            job_poll_timeout_seconds=_resolved_float(
                config,
                "job_poll_timeout_seconds",
                DEFAULT_COLLIBRA_JOB_POLL_TIMEOUT_SECONDS,
            ),
            execution_mode=_resolved_str(config, "execution_mode", DEFAULT_COLLIBRA_EXECUTION_MODE),
            synchronization_id=_resolved_str(config, "synchronization_id"),
            batch_max_resources=_resolved_int(
                config, "batch_max_resources", DEFAULT_COLLIBRA_BATCH_MAX_RESOURCES
            ),
            batch_max_additional_characteristics=_resolved_int(
                config,
                "batch_max_additional_characteristics",
                DEFAULT_COLLIBRA_BATCH_MAX_ADDITIONAL_CHARACTERISTICS,
            ),
            source_name=_resolved_str(config, "source_name", DEFAULT_POSTGRES_SOURCE_NAME),
        )
    except ValueError as exc:
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/",
                    message=str(exc),
                )
            ]
        ) from exc


def _remote_state_read_factory(
    context: ProviderRuntimeContext,
) -> _CollibraRemoteStateReadCapability:
    return _CollibraRemoteStateReadCapability(_runtime_from_context(context))


def _target_planning_factory(
    context: ProviderRuntimeContext,
) -> _CollibraTargetPlanningCapability:
    return _CollibraTargetPlanningCapability(_runtime_from_context(context))


def _compatibility_preflight_factory(
    context: ProviderRuntimeContext,
) -> _CollibraCompatibilityPreflightCapability:
    return _CollibraCompatibilityPreflightCapability(_runtime_from_context(context))


def _authorized_mutation_factory(
    context: ProviderRuntimeContext,
) -> _CollibraAuthorizedMutationCapability:
    return _CollibraAuthorizedMutationCapability(_runtime_from_context(context))


def register() -> ProviderRegistration:
    descriptor = ProviderDescriptor(
        provider_id="collibra",
        display_name="Collibra",
        provider_version=package_version,
        sdk_compatibility="==1",
        capabilities=(
            CapabilityId.REMOTE_STATE_READ,
            CapabilityId.TARGET_PLANNING,
            CapabilityId.COMPATIBILITY_PREFLIGHT,
            CapabilityId.AUTHORIZED_MUTATION,
        ),
    )
    return ProviderRegistration(
        descriptor=descriptor,
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.REMOTE_STATE_READ,
                factory=_remote_state_read_factory,
            ),
            CapabilityBinding(
                capability_id=CapabilityId.TARGET_PLANNING,
                factory=_target_planning_factory,
            ),
            CapabilityBinding(
                capability_id=CapabilityId.COMPATIBILITY_PREFLIGHT,
                factory=_compatibility_preflight_factory,
            ),
            CapabilityBinding(
                capability_id=CapabilityId.AUTHORIZED_MUTATION,
                factory=_authorized_mutation_factory,
            ),
        ),
        config_validator=_CollibraConfigValidator(),
    )


__all__ = [
    "CollibraMutationRequest",
    "register",
]

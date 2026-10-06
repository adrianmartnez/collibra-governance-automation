"""Built-in PostgreSQL metadata discovery provider."""

from __future__ import annotations

from collections.abc import Mapping

from governance import __version__ as package_version
from governance.config import _parse_database_url
from governance.domain.models import GovernanceModel
from governance.providers.builtins._common import is_env_ref, require_env_ref
from governance.providers.capabilities import CapabilityId
from governance.providers.contracts import (
    CapabilityBinding,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRuntimeContext,
)
from governance.providers.errors import CODE_INVALID_DESCRIPTOR, ProviderDiagnostic, ProviderError
from governance.scanner.postgres import PostgresConnectionParams, PostgresMetadataScanner

_DISCRETE_KEYS = frozenset({"host", "port", "database", "user", "password"})


class PostgresMetadataDiscovery:
    """Provider-bound metadata discovery collaborator (operational I/O on ``discover``)."""

    def __init__(self, params: PostgresConnectionParams) -> None:
        self._params = params

    def discover(self) -> GovernanceModel:
        return PostgresMetadataScanner(self._params).scan()


class _PostgresqlConfigValidator:
    def validate(self, config: Mapping[str, object]) -> None:
        diagnostics: list[ProviderDiagnostic] = []

        source_name = config.get("source_name")
        if not isinstance(source_name, str) or not source_name.strip():
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/source_name",
                    message="source_name must be a non-empty string",
                )
            )

        has_url = "database_url" in config and config.get("database_url") is not None
        discrete_present = any(
            key in config and config.get(key) is not None for key in _DISCRETE_KEYS
        )

        if has_url and discrete_present:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/",
                    message=(
                        "connection must use database_url or discrete host/port/database/user/"
                        "password fields, not both"
                    ),
                )
            )
        elif has_url:
            url_value = config.get("database_url")
            if is_env_ref(url_value):
                try:
                    require_env_ref("/database_url", url_value)
                except ProviderError as exc:
                    diagnostics.extend(exc.errors)
            else:
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path="/database_url",
                        message="database_url must be an environment reference",
                    )
                )
        elif discrete_present:
            for key in ("host", "database", "user", "password"):
                if config.get(key) is None:
                    diagnostics.append(
                        ProviderDiagnostic(
                            code=CODE_INVALID_DESCRIPTOR,
                            path=f"/{key}",
                            message=f"{key} is required for discrete connection config",
                        )
                    )
            password = config.get("password")
            if password is not None and not is_env_ref(password):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path="/password",
                        message="password must be an environment reference",
                    )
                )
            port = config.get("port")
            if port is not None and not isinstance(port, int):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path="/port",
                        message="port must be an integer when provided",
                    )
                )
        else:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/",
                    message=(
                        "connection requires database_url or discrete host/port/database/user/"
                        "password fields"
                    ),
                )
            )

        if diagnostics:
            raise ProviderError(diagnostics)


def _connection_params_from_config(config: Mapping[str, object]) -> PostgresConnectionParams:
    source_name = str(config["source_name"]).strip()
    database_url = config.get("database_url")
    if database_url is not None:
        parsed = _parse_database_url(str(database_url))
        return PostgresConnectionParams(
            host=str(parsed["postgres_host"]),
            port=int(parsed["postgres_port"]),
            db=str(parsed["postgres_db"]),
            user=str(parsed["postgres_user"]),
            password=str(parsed["postgres_password"]),
            source_name=source_name,
        )
    port_raw = config.get("port", 5432)
    port = int(port_raw) if isinstance(port_raw, int) else 5432
    return PostgresConnectionParams(
        host=str(config["host"]),
        port=port,
        db=str(config["database"]),
        user=str(config["user"]),
        password=str(config["password"]),
        source_name=source_name,
    )


def _metadata_discovery_factory(context: ProviderRuntimeContext) -> PostgresMetadataDiscovery:
    params = _connection_params_from_config(context.config)
    return PostgresMetadataDiscovery(params)


def register() -> ProviderRegistration:
    descriptor = ProviderDescriptor(
        provider_id="postgresql",
        display_name="PostgreSQL",
        provider_version=package_version,
        sdk_compatibility="==1",
        capabilities=(CapabilityId.METADATA_DISCOVERY,),
    )
    return ProviderRegistration(
        descriptor=descriptor,
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.METADATA_DISCOVERY,
                factory=_metadata_discovery_factory,
            ),
        ),
        config_validator=_PostgresqlConfigValidator(),
    )

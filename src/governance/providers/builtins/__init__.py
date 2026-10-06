"""Built-in provider registrations shipped with the core package."""

from __future__ import annotations

from governance.providers.builtins.collibra import register as register_collibra
from governance.providers.builtins.dbt import register as register_dbt
from governance.providers.builtins.odcs import register as register_odcs
from governance.providers.builtins.openlineage import register as register_openlineage
from governance.providers.builtins.postgresql import register as register_postgresql
from governance.providers.contracts import ProviderRegistration


def builtin_provider_registrations() -> tuple[ProviderRegistration, ...]:
    """Return all built-in source provider registrations, sorted by ``provider_id``."""
    registrations = (
        register_collibra(),
        register_dbt(),
        register_odcs(),
        register_openlineage(),
        register_postgresql(),
    )
    return tuple(sorted(registrations, key=lambda item: item.descriptor.provider_id))


__all__ = ["builtin_provider_registrations"]

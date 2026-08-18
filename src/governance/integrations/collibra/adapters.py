"""Shared Collibra adapter contract and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
    CollibraRemoteState,
)

if TYPE_CHECKING:
    from governance.config import Settings


class CollibraAdapterError(RuntimeError):
    """Structured adapter failure that omits credential material."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        status_code: int | None = None,
        endpoint_path: str | None = None,
        endpoint_family: str | None = None,
        attempt: int | None = None,
        exhausted: bool | None = None,
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.endpoint_path = endpoint_path
        self.endpoint_family = endpoint_family
        self.attempt = attempt
        self.exhausted = exhausted
        parts = [message, f"operation={operation}"]
        if status_code is not None:
            parts.append(f"status_code={status_code}")
        if endpoint_path is not None:
            parts.append(f"endpoint_path={endpoint_path}")
        if endpoint_family is not None:
            parts.append(f"endpoint_family={endpoint_family}")
        if attempt is not None:
            parts.append(f"attempt={attempt}")
        if exhausted is not None:
            parts.append(f"exhausted={exhausted}")
        super().__init__("; ".join(parts))


class CollibraAuthError(CollibraAdapterError):
    """Authentication-boundary failure that omits tokens and client secrets."""


@runtime_checkable
class CollibraAdapter(Protocol):
    """Application-facing Collibra transport boundary.

    Adapters perform remote I/O and never decide which changes belong in a sync
    plan. Diff and execution live in ``sync``.
    """

    @property
    def mode(self) -> Literal["mock", "live"]: ...

    def read_remote_state(self, desired: CollibraDesiredState) -> CollibraRemoteState: ...

    def create_asset(self, asset: CollibraAssetSpec) -> str: ...

    def update_asset(
        self,
        remote_id: str,
        asset: CollibraAssetSpec,
        *,
        patch_name: bool = True,
        patch_display_name: bool = True,
    ) -> None: ...

    def create_relationship(
        self,
        relationship: CollibraRelationshipSpec,
        *,
        source_remote_id: str,
        target_remote_id: str,
    ) -> str: ...


def build_collibra_adapter(
    settings: Settings,
    mapping_config: CollibraMappingConfig,
    *,
    transport: object | None = None,
) -> CollibraAdapter:
    """Construct mock or live adapter from settings."""
    mode = settings.collibra_mode.strip().lower()
    if mode == "mock":
        from governance.integrations.collibra.mock import MockCollibraAdapter

        return MockCollibraAdapter(mapping_config)
    if mode == "live":
        from governance.integrations.collibra.live import LiveCollibraAdapter

        return LiveCollibraAdapter.from_settings(
            settings,
            mapping_config,
            transport=transport,
        )
    raise ValueError("collibra_mode must be 'mock' or 'live'")

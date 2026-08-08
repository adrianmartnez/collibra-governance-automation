"""Canonical governance snapshot artifact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.domain import GovernanceModel
from governance.exporters.inventory import SCANNER_CONTRACT_VERSION
from governance.identity import ContentIdentity, snapshot_identity

SNAPSHOT_SCHEMA = "governance-snapshot"
SNAPSHOT_VERSION = "1"


@dataclass(frozen=True, slots=True)
class GovernanceSnapshot:
    model: GovernanceModel
    source_name: str
    database_name: str
    system_type: str = "postgresql"
    scanner: str = "postgresql"
    scanner_contract_version: str = SCANNER_CONTRACT_VERSION

    @classmethod
    def from_model(cls, model: GovernanceModel) -> GovernanceSnapshot:
        if len(model.data_sources) != 1:
            raise ValueError("GovernanceModel must contain exactly one DataSource")
        data_source = model.data_sources[0]
        if len(data_source.databases) != 1:
            raise ValueError("DataSource must contain exactly one Database")
        database = data_source.databases[0]
        return cls(
            model=model,
            source_name=data_source.name,
            database_name=database.name,
            system_type=data_source.system_type,
            scanner=data_source.system_type,
            scanner_contract_version=SCANNER_CONTRACT_VERSION,
        )

    def canonical_dict_without_identity(self) -> dict[str, Any]:
        return {
            "governance": self.model.to_dict(),
            "scan": {
                "scanner": self.scanner,
                "scanner_contract_version": self.scanner_contract_version,
            },
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "snapshot_version": SNAPSHOT_VERSION,
            "source": {
                "database": self.database_name,
                "name": self.source_name,
                "system_type": self.system_type,
            },
        }

    def content_identity(self) -> ContentIdentity:
        return snapshot_identity(self.canonical_dict_without_identity())

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict_without_identity()
        payload["content_identity"] = self.content_identity().to_dict()
        return payload

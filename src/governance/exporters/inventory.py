"""Deterministic metadata inventory export."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governance.domain import GovernanceModel
from governance.io.atomic import atomic_write_text

INVENTORY_SCHEMA = "governance-metadata-inventory"
INVENTORY_VERSION = "1.0"
SCANNER_CONTRACT_VERSION = "1"


class InventoryExportError(RuntimeError):
    """Raised when inventory export fails."""


@dataclass(frozen=True, slots=True)
class MetadataInventory:
    model: GovernanceModel
    source_name: str
    database_name: str
    system_type: str = "postgresql"
    scanner: str = field(default="postgresql", init=False)
    scanner_contract_version: str = field(
        default=SCANNER_CONTRACT_VERSION,
        init=False,
    )

    @classmethod
    def from_model(cls, model: GovernanceModel) -> MetadataInventory:
        if len(model.data_sources) != 1:
            raise ValueError("GovernanceModel must contain exactly one DataSource")
        data_source = model.data_sources[0]
        if len(data_source.databases) != 1:
            raise ValueError("DataSource must contain exactly one Database")
        if data_source.system_type != "postgresql":
            raise ValueError('DataSource.system_type must be "postgresql"')
        database = data_source.databases[0]
        return cls(
            model=model,
            source_name=data_source.name,
            database_name=database.name,
            system_type=data_source.system_type,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory_schema": INVENTORY_SCHEMA,
            "inventory_version": INVENTORY_VERSION,
            "source": {
                "database": self.database_name,
                "name": self.source_name,
                "system_type": self.system_type,
            },
            "scan": {
                "scanner": self.scanner,
                "scanner_contract_version": self.scanner_contract_version,
            },
            "governance": self.model.to_dict(),
        }

    def to_json(self) -> str:
        return (
            json.dumps(
                self.to_dict(),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n"
        )


def write_inventory(inventory: MetadataInventory, output_path: str | Path) -> Path:
    target = Path(output_path)
    if target.exists() and target.is_dir():
        raise InventoryExportError(f"Inventory output path is a directory: {target}")
    try:
        return atomic_write_text(target, inventory.to_json())
    except OSError as exc:
        raise InventoryExportError(f"Unable to write inventory to {target}") from exc

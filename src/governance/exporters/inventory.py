"""Deterministic metadata inventory export."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governance.domain import GovernanceModel

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

    payload = inventory.to_json()
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InventoryExportError(
            f"Unable to create inventory parent directory: {parent}"
        ) from exc

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        return target
    except InventoryExportError:
        raise
    except OSError as exc:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise InventoryExportError(f"Unable to write inventory to {target}") from exc
    except Exception:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise InventoryExportError(f"Unable to write inventory to {target}") from None

# collibra-governance-automation

Technical metadata governance tooling with Python and PostgreSQL, using a vendor-neutral model and explicit boundaries for catalog integrations. The current implementation discovers PostgreSQL technical metadata from system catalogs and exports deterministic inventory JSON; Collibra integration remains planned.

**Stack:** Python 3.12 · PostgreSQL 16 · Psycopg 3 · Docker Compose

## Current capabilities

- Reproducible PostgreSQL governance demo with fictional metadata-rich relational structures
- PostgreSQL technical metadata discovery directly from system catalogs
- Vendor-neutral governance model for sources, databases, schemas, tables, columns, keys, relationships, and ownership
- Primary-key and foreign-key discovery, including composite constraints and self-references
- Table and column comments plus database, schema, and table ownership metadata
- Stable logical identifiers independent of host, port, credentials, or PostgreSQL OIDs
- Deterministic, versioned metadata inventory with human-reviewable JSON export
- Automated quality gates for linting, unit tests, package validation, PostgreSQL reproducibility, and metadata integration

## Project status

Metadata discovery and deterministic inventory generation are implemented. Catalog-specific mapping and synchronization remain separate later stages.

| Current | Planned |
| --- | --- |
| Python package foundation | Collibra asset and relationship mapping |
| Reproducible PostgreSQL governance demo | Mock and live Collibra adapters |
| Vendor-neutral governance domain model | Catalog-state diff |
| PostgreSQL system-catalog discovery | Safe synchronization |
| Deterministic metadata inventory | End-to-end governance CLI |
| Five automated quality gates | Release documentation |

Tracking: [v1.0 - Governance Automation MVP](https://github.com/fgnfmackk/collibra-governance-automation/milestone/1)

Pipeline:

```text
PostgreSQL
  -> metadata discovery                 [current]
  -> vendor-neutral governance model    [current]
  -> deterministic inventory            [current]
  -> Collibra mapping                    [planned]
  -> mock/live adapters                 [planned]
  -> diff + safe sync                    [planned]
  -> end-to-end CLI                      [planned]
```

## Design principles

- **Vendor-neutral core model** — discovered metadata is represented independently of PostgreSQL drivers and catalog-specific API types.
- **Read-only discovery** — scanner queries run against PostgreSQL metadata catalogs inside a read-only transaction and do not inspect business-row contents.
- **Consistent metadata snapshot** — each scan uses one repeatable-read transaction so related catalog queries observe a coherent database state.
- **Deterministic state** — stable logical IDs and ordered serialization make equivalent metadata produce equivalent output.
- **Bulk catalog access** — discovery reads metadata in a fixed set of catalog queries rather than issuing per-table or per-column queries.
- **Reproducible local environment** — Docker Compose and versioned SQL initialize the same fictional demo database from a clean volume.
- **Safe-by-default synchronization (planned)** — future catalog writes will be plan-driven and non-destructive by default; synchronization is not implemented yet.

## Requirements

- Python 3.12+
- Docker with Docker Compose

## Quick start

Create and activate a virtual environment.

### Bash / Linux / macOS / WSL / Git Bash

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m governance
```

### PowerShell / Windows

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m governance
```

Copy `.env.example` to `.env` only when local overrides are needed. The defaults match the Docker Compose demo environment.

## PostgreSQL demo

Start the demo database:

```bash
docker compose up -d --wait
```

Local credentials are intentionally fictional and development-only:

| Setting | Value |
| --- | --- |
| Host | `localhost` |
| Port | `5432` |
| Database | `governance_demo` |
| User | `postgres` |
| Password | `postgres` |
| Logical source | `governance-demo` |

The primary demo schema is `commerce`, containing:

- `customers`
- `products`
- `employees`
- `orders`
- `order_items`
- `payments`
- `marketing_contacts`

Seed data is synthetic and uses reserved example data such as `@example.com`. Its purpose is to provide a realistic metadata structure, not production-like volume.

Verify the demo contract from Bash, WSL, or Git Bash:

```bash
bash sample/verify_demo.sh
```

## Metadata discovery

`PostgresMetadataScanner` reads PostgreSQL system catalogs and maps the connected database into the vendor-neutral `GovernanceModel`.

Discovery includes:

- user schemas
- ordinary and partitioned tables
- columns and PostgreSQL-formatted data types
- nullability and ordinal position
- primary keys
- foreign keys
- table and column comments
- database, schema, and table ownership
- table-to-table relationships derived from foreign keys

The scanner does not sample, profile, count, or otherwise read business-row contents.

```python
from governance.config import load_settings
from governance.scanner import PostgresMetadataScanner

settings = load_settings()
model = PostgresMetadataScanner(settings).scan()

print(model.to_json())
```

The logical source name comes from `POSTGRES_SOURCE_NAME`. Host, port, credentials, and PostgreSQL OIDs are intentionally excluded from public governance identifiers.

## Metadata inventory

The discovered governance model can be wrapped in a deterministic, versioned inventory and written as UTF-8 JSON.

```python
from governance.config import load_settings
from governance.exporters import MetadataInventory, write_inventory
from governance.scanner import PostgresMetadataScanner

settings = load_settings()
model = PostgresMetadataScanner(settings).scan()
inventory = MetadataInventory.from_model(model)

write_inventory(inventory, settings.inventory_output_path)
```

Default output:

```text
artifacts/metadata-inventory.json
```

The generated `artifacts/` directory is ignored by Git.

Inventory envelope:

```json
{
  "governance": {},
  "inventory_schema": "governance-metadata-inventory",
  "inventory_version": "1.0",
  "scan": {
    "scanner": "postgresql",
    "scanner_contract_version": "1"
  },
  "source": {
    "database": "governance_demo",
    "name": "governance-demo",
    "system_type": "postgresql"
  }
}
```

The `governance` object contains the complete vendor-neutral metadata graph. Equivalent metadata produces equivalent serialized inventory output; volatile execution data such as timestamps, OIDs, host details, and credentials is excluded.

## Local validation

Python quality and unit tests:

```bash
ruff check src tests
ruff format --check src tests
pytest -m "not integration"
python -m governance
python -m build
```

PostgreSQL demo contract:

```bash
docker compose config -q
docker compose down -v
docker compose up -d --wait
bash sample/verify_demo.sh
```

Metadata integration:

```bash
pytest -m integration
```

Final cleanup:

```bash
docker compose down -v
```

CI runs five independent GitHub-hosted `ubuntu-latest` jobs:

- `lint`
- `unit-tests`
- `package-validation`
- `postgres-integration`
- `metadata-integration`

## License

MIT

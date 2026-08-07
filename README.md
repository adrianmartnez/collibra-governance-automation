# collibra-governance-automation

Technical metadata governance tooling with Python and PostgreSQL, using a vendor-neutral model and explicit boundaries for catalog integrations. The current implementation discovers PostgreSQL technical metadata, exports deterministic inventory JSON, maps inventory into an inspectable Collibra-oriented desired state, provides mock and live Core REST API v2 adapter boundaries, and executes plan-driven dry-run/apply synchronization without automatic deletes.

**Stack:** Python 3.12 · PostgreSQL 16 · Psycopg 3 · httpx · Docker Compose

## Current capabilities

- Reproducible PostgreSQL governance demo with fictional metadata-rich relational structures
- PostgreSQL technical metadata discovery directly from system catalogs
- Vendor-neutral governance model for sources, databases, schemas, tables, columns, keys, relationships, and ownership
- Primary-key and foreign-key discovery, including composite constraints and self-references
- Table and column comments plus database, schema, and table ownership metadata
- Stable logical identifiers independent of host, port, credentials, or PostgreSQL OIDs
- Deterministic, versioned metadata inventory with human-reviewable JSON export
- Collibra-oriented asset and relationship mapping with deterministic desired state
- Mock Collibra adapter for local, offline demonstration
- Live Collibra Core REST API v2 adapter boundary (contract-tested; tenant validation not claimed)
- Plan-driven metadata diff with dry-run and explicit apply
- No automatic remote deletes; unmanaged tenant objects are ignored
- Automated quality gates for linting, unit tests, package validation, PostgreSQL reproducibility, metadata integration, and Collibra mock lifecycle

## Project status

| Current | Planned |
| --- | --- |
| Python package foundation | End-to-end governance CLI |
| Reproducible PostgreSQL governance demo | Release documentation |
| Vendor-neutral governance domain model | |
| PostgreSQL system-catalog discovery | |
| Deterministic metadata inventory | |
| Collibra asset and relationship mapping | |
| Mock and live Collibra adapters | |
| Safe plan-driven synchronization | |
| Six automated quality gates | |

Tracking: [v1.0 - Governance Automation MVP](https://github.com/fgnfmackk/collibra-governance-automation/milestone/1)

Pipeline:

```text
PostgreSQL
  -> metadata discovery                 [current]
  -> vendor-neutral governance model    [current]
  -> deterministic inventory            [current]
  -> Collibra mapping                    [current]
  -> mock/live adapters                 [current]
  -> diff + safe sync                    [current]
  -> end-to-end CLI                      [planned]
```

## Design principles

- **Vendor-neutral core model** — discovered metadata is represented independently of PostgreSQL drivers and catalog-specific API types.
- **Read-only discovery** — scanner queries run against PostgreSQL metadata catalogs inside a read-only transaction and do not inspect business-row contents.
- **Consistent metadata snapshot** — each scan uses one repeatable-read transaction so related catalog queries observe a coherent database state.
- **Deterministic state** — stable logical IDs and ordered serialization make equivalent metadata produce equivalent output.
- **Bulk catalog access** — discovery reads metadata in a fixed set of catalog queries rather than issuing per-table or per-column queries.
- **Reproducible local environment** — Docker Compose and versioned SQL initialize the same fictional demo database from a clean volume.
- **Safe-by-default synchronization** — catalog writes require an explicit plan, default to dry-run, update only supported managed fields, and never delete automatically.

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

Copy `.env.example` to `.env` only when local overrides are needed. The defaults match the Docker Compose demo environment. Collibra defaults to mock mode and require no credentials.

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

## Collibra mapping

The mapping layer translates a `GovernanceModel` or `MetadataInventory` into an inspectable `CollibraDesiredState` without network calls or PostgreSQL access.

Tenant-specific Collibra domain, asset-type, relation-type, and attribute-type references are supplied through `CollibraMappingConfig`. Mock helpers use symbolic `mock:*` refs that are local identifiers, not commercial tenant UUIDs.

Mapped assets use:

- `local_id` — stable governance identifier used for reconciliation
- `name` — deterministic full name (`database`, `database.schema`, `database.schema.table`, `database.schema.table.column`)
- `display_name` — short original object name

```python
from governance.config import load_settings
from governance.exporters import MetadataInventory
from governance.integrations.collibra import map_to_desired_state, mock_mapping_config
from governance.scanner import PostgresMetadataScanner

settings = load_settings()
model = PostgresMetadataScanner(settings).scan()
inventory = MetadataInventory.from_model(model)
desired = map_to_desired_state(inventory, mock_mapping_config())
print(desired.to_json())
```

## Collibra adapters

Adapters share one application-facing contract for reading remote state and applying explicit create/update operations. Diff/sync planning is separate.

### Mock mode

- Local deterministic state, no network, no Collibra tenant
- Clearly identified as `mode == "mock"`
- Uses symbolic `mock:*` mapping refs and deterministic mock remote IDs

### Live mode

- HTTP client for Collibra Core REST API v2 (`/rest/2.0`)
- Supports Basic username/password or a caller-supplied Bearer token
- Core REST may accept Bearer/JWT tokens that originate from OAuth elsewhere; this project does not acquire, refresh, or cache OAuth tokens
- Requires `COLLIBRA_BASE_URL` and exactly one auth method
- Finite timeout (default 10s), TLS verification on
- Reads are scoped to the configured domain and asset types (`whole_tenant_scan=false`)
- Attribute updates are directed (create/patch managed attributes only); tenant-specific attributes and unmanaged relations are not replaced or deleted
- Remote-state discovery is paginated and deterministic after retrieval, but Collibra REST reads are not treated as a transactional snapshot across concurrent tenant mutations
- Contract-tested with mocked HTTP; not validated against a commercial Collibra tenant in this repository

## Safe synchronization

Synchronization always builds an explicit `SyncPlan` before any write:

1. `adapter.read_remote_state(desired)`
2. `build_sync_plan(desired, remote)`
3. `execute_sync_plan(adapter, plan, apply=False)` for dry-run
4. `execute_sync_plan(adapter, plan, apply=True)` only after review

Safety rules:

- dry-run performs zero writes
- no automatic DELETE actions
- only managed objects (configured domain/types + stable `local_id` attribute) participate in diff/sync
- unmanaged tenant assets/relations are ignored and never adopted by name
- `REMOTE_ONLY` reports managed remote objects absent from desired state without deleting them
- REST sync is not a distributed transaction

```python
from governance.config import load_settings
from governance.exporters import MetadataInventory
from governance.integrations.collibra import (
    build_collibra_adapter,
    build_sync_plan,
    execute_sync_plan,
    map_to_desired_state,
    mock_mapping_config,
)
from governance.scanner import PostgresMetadataScanner

settings = load_settings()
model = PostgresMetadataScanner(settings).scan()
desired = map_to_desired_state(MetadataInventory.from_model(model), mock_mapping_config())
adapter = build_collibra_adapter(settings, mock_mapping_config())

remote = adapter.read_remote_state(desired)
plan = build_sync_plan(desired, remote)
dry_run = execute_sync_plan(adapter, plan, apply=False)
result = execute_sync_plan(adapter, plan, apply=True)
```

## Local validation

Python quality and unit tests:

```bash
ruff check src tests
ruff format --check src tests
pytest -m "not integration and not collibra_integration"
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

Collibra mock lifecycle:

```bash
pytest -m collibra_integration
```

Final cleanup:

```bash
docker compose down -v
```

CI runs six independent GitHub-hosted `ubuntu-latest` jobs:

- `lint`
- `unit-tests`
- `package-validation`
- `postgres-integration`
- `metadata-integration`
- `collibra-integration`

## License

MIT

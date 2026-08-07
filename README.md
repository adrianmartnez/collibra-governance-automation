# collibra-governance-automation

Technical metadata governance tooling with Python and PostgreSQL, using a vendor-neutral model and explicit boundaries for Collibra integration. Metadata discovery, deterministic inventory generation, and Collibra adapters are planned next.

**Stack:** Python 3.12 · PostgreSQL 16 · Docker Compose

## Current capabilities

- Installable Python package under `src/governance` with explicit environment-based configuration
- Reproducible local PostgreSQL environment with a fictional commerce schema for technical metadata workflows
- Vendor-neutral governance domain model covering sources, databases, schemas, tables, columns, keys, relationships, and ownership
- Deterministic identifiers and serialization for stable governance state
- Automated quality gates for linting, tests, package validation, and PostgreSQL integration

## Project status

The governance foundation is in place. The next stages add metadata discovery, deterministic inventory generation, and catalog-specific integration.

| Current | Planned |
| --- | --- |
| Python package foundation | PostgreSQL metadata discovery |
| Reproducible PostgreSQL governance demo | Deterministic metadata inventory (JSON) |
| Vendor-neutral governance domain model | Collibra mapping and adapters |
| Deterministic IDs and serialization | Diff and safe synchronization |
| Automated quality gates | End-to-end CLI |

Tracking: [v1.0 - Governance Automation MVP](https://github.com/fgnfmackk/collibra-governance-automation/milestone/1)

Planned pipeline:

```text
PostgreSQL
  -> metadata discovery
  -> vendor-neutral governance model
  -> deterministic inventory
  -> Collibra mapping
  -> mock/live adapters
  -> diff + safe sync
  -> CLI + CI + docs
```

## Design principles

- **Vendor-neutral core model** — governance entities remain independent of PostgreSQL drivers and Collibra API types.
- **Deterministic state** — stable identifiers and ordered serialization make equivalent metadata produce equivalent governance state.
- **Reproducible local environment** — Docker Compose and versioned SQL initialize the same fictional database from a clean volume.
- **Clear layer boundaries** — source discovery, governance modeling, and vendor-specific integrations remain separate concerns.
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

Copy `.env.example` to `.env` only when local overrides are needed. The default application settings already match the Docker Compose demo service.

## PostgreSQL demo

Start the reproducible demo database:

```bash
docker compose up -d --wait
```

Local demo credentials are intentionally fictional and development-only:

| Setting | Value |
| --- | --- |
| Host | `localhost` |
| Port | `5432` |
| Database | `governance_demo` |
| User | `postgres` |
| Password | `postgres` |

Schema: `commerce`

Tables:

- `customers`
- `products`
- `employees`
- `orders`
- `order_items`
- `payments`
- `marketing_contacts`

Seed data is synthetic and uses reserved example data such as `@example.com`. Its purpose is to provide useful technical metadata structure, not production-like volume.

The metadata contract can be verified from Bash, WSL, or Git Bash:

```bash
bash sample/verify_demo.sh
```

Remove the demo database and its volume with:

```bash
docker compose down -v
```

## Local validation

Python checks:

```bash
ruff check src tests
ruff format --check src tests
pytest
python -m governance
python -m build
```

The package-validation CI job builds the distribution, installs the wheel in a clean virtual environment, and verifies both package import and module execution.

PostgreSQL integration checks:

```bash
docker compose config -q
docker compose down -v
docker compose up -d --wait
bash sample/verify_demo.sh
docker compose down -v
docker compose up -d --wait
bash sample/verify_demo.sh
docker compose down -v
```

The integration helper is Bash-based; on Windows, run it from WSL or Git Bash.

CI runs four independent jobs on GitHub-hosted `ubuntu-latest`:

- `lint`
- `unit-tests`
- `package-validation`
- `postgres-integration`

## License

MIT

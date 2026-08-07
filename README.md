# collibra-governance-automation

Automate technical metadata governance for relational databases using Python and PostgreSQL, with a path toward Collibra-compatible catalog integration.

## Project status

Python package foundation and local configuration conventions are in place under `src/governance`.

Planned next: reproducible PostgreSQL demo dataset, vendor-neutral domain model, metadata discovery, and catalog integration workflows.

## Requirements

- Python 3.12+
- Docker and Docker Compose (for later demo database steps)

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -e ".[dev]"
python -m governance
```

Copy `.env.example` to `.env` for local overrides.

## Local validation

```bash
ruff check src tests
ruff format --check src tests
pytest
python -m governance
```

## License

MIT

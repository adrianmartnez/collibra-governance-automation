# Example Catalog Provider — third-party proof for the Governance Provider SDK

Independent Python distribution that registers `example.catalog` through the
public `governance.providers` entry-point group.

## Capabilities

- `governance_graph`
- `property_observations`

No `metadata_discovery` (therefore not used with `governance check`).

## Requires

A host install of `collibra-governance-automation` that provides Provider SDK API `1`
(`governance.providers`, `governance.domain`, `governance.conformance`).

Until v2.0 is published on PyPI, install the core from an exact Git commit SHA
(see CI). Do **not** depend on `collibra-governance-automation>=1.4` — published
1.4.0 does not include the Provider SDK.

## Install (development)

```bash
python -m venv .venv
source .venv/bin/activate
pip install "collibra-governance-automation @ git+https://github.com/adrianmartnez/collibra-governance-automation.git@<EXACT_SHA>"
pip install -e ".[dev]"
pytest
```

## Sample impact

```bash
governance impact \
  --config sample/governance.yaml \
  --namespace demo \
  --changes sample/changes.json \
  --format json
```

Do not pass `--odcs` / `--dbt-manifest` / `--openlineage`.

## Trust

Installed providers are trusted Python dependencies. This package is not sandboxed.

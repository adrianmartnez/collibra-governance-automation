# Contributing

Bug reports, documentation fixes, Provider SDK feedback, provider/integration proposals, and focused code contributions are welcome.

For large changes or new providers/integrations, please open a GitHub issue first so scope and compatibility can be discussed before implementation.

## Development setup

Requires Python `>=3.12`.

```bash
git clone https://github.com/adrianmartnez/collibra-governance-automation.git
cd collibra-governance-automation
python -m venv .venv
```

Bash / Linux / macOS / WSL / Git Bash:

```bash
source .venv/bin/activate
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

For the local PostgreSQL demo integration:

```bash
docker compose up -d --wait
bash sample/verify_demo.sh
```

Cleanup:

```bash
docker compose down -v
```

Docker is not required for every test run. Unit and packaging checks can run without Compose.

## Quality checks

Typical local checks:

```bash
ruff check src tests
ruff format --check src tests
pytest -m "not integration and not collibra_integration and not cli_integration and not collibra_contract and not provider_packaging"
python -m build
pytest -m provider_packaging
```

When changing integration behavior, also run the relevant markers (for example `integration`, `collibra_integration`, `cli_integration`, or `collibra_contract`) as appropriate.

Documentation-only changes do not require the full Docker-backed suite.

## Pull requests

Please keep pull requests:

- small and clearly scoped;
- motivated with the intended behavior;
- covered by tests when behavior changes;
- accompanied by docs updates when public surfaces change;
- deterministic and free of unrelated refactors;
- free of secrets or credentials;
- free of incidental machine-contract version changes;
- compatible with supported `governance.yaml` v1 unless an explicit compatibility decision is documented.

## Provider contributions

External providers should use only the public surfaces:

```text
governance.providers
governance.domain
governance.conformance
```

The current Provider SDK API is `"1"`.

See:

- [Provider author guide](docs/providers/author-guide.md)
- Companion template: https://github.com/adrianmartnez/governance-provider-example

Expectations:

- declare capabilities explicitly;
- full conformance must cover every declared capability;
- installed providers are trusted Python dependencies and are not sandboxed;
- conformance validates cooperative behavior; it is not a security certification;
- prefer an independent provider package when an integration does not need to live in core.

## Safety expectations

Preserve the core safety model:

- dry-run by default;
- plan before apply;
- explicit `--apply` for mutation;
- live writes additionally require `--confirm-live`;
- stale-plan protection;
- no automatic destructive reconciliation;
- applicable unresolved conflicts remain blockers;
- providers must not silently bypass core authorization.

## Reporting bugs

Use GitHub Issues:

https://github.com/adrianmartnez/collibra-governance-automation/issues

Include a minimal reproducible example and relevant package/Python versions.

Do not post secrets, tokens, credentials, or personal data.

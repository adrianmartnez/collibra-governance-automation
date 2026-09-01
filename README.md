# collibra-governance-automation

[![CI](https://github.com/adrianmartnez/collibra-governance-automation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/adrianmartnez/collibra-governance-automation/actions/workflows/ci.yml)

Deterministic Governance-as-Code and governance change intelligence between metadata sources, open standards, policies, lineage, pull-request review, and governance platforms.

Flow:

```text
metadata / contracts / lineage
-> vendor-neutral governance graph
-> governance change intelligence (check / impact / plan)
-> CLI and GitHub PR review
-> safe reconciliation (Collibra integration boundary)
```

This is not another data catalog, not only a crawler, and not a Collibra replacement. The core model is vendor-neutral. Collibra remains a first-class integration for mapping, mock/live adapters, and plan-driven synchronization. Impact analysis is read-only (`writes_performed=0`); it does not apply or remediate.

**Stack:** Python 3.12 · PostgreSQL 16 · Psycopg 3 · httpx · Docker Compose · MIT

**Package version:** `1.3.0`. See [CHANGELOG.md](CHANGELOG.md). Tagged releases are published from reviewed `main` commits and are available through GitHub Releases. Package SemVer is distinct from versioned machine contracts such as `governance-action-result` v1 and `governance-impact-result` v1.

## What is implemented

| Area | Status |
| --- | --- |
| PostgreSQL technical metadata discovery | Implemented (local PostgreSQL demo) |
| Vendor-neutral governance model | Implemented |
| Governance graph + provenance foundation | Deterministic |
| Deterministic inventory / snapshot artifacts | Implemented |
| Governance-as-Code (`governance.yaml`, policies, saved plans) | Implemented (opt-in via `--config`) |
| Open Data Contract Standard (ODCS) ingestion | Implemented |
| dbt manifest metadata + dependency edges | Implemented |
| OpenLineage events + dataset facets | Implemented |
| Deterministic column-level lineage | Implemented |
| Downstream traversal / blast-radius analysis | Deterministic |
| `governance impact` CLI + impact changes/result v1 artifacts | Contract-tested |
| `governance compare` CLI + snapshot-comparison v1 artifacts | Implemented (offline; difference ≠ drift) |
| `governance drift` CLI + drift-result v1 artifacts | Implemented (offline; explicit policy required when different) |
| GitHub Action `operation: impact` + PR/step-summary reports | Implemented (read-only) |
| Collibra mapping + mock adapter | Implemented (local/offline) |
| Live Collibra Core REST API v2 adapter | Contract-tested (localhost HTTP server; no commercial tenant) |
| OAuth client credentials (native Collibra + external IdP) | Implemented |
| Import API v2 / sync_v2 job lifecycle | Implemented |
| Deterministic production batching | Implemented (conservative ceilings) |
| `governance preflight` (read-only) | Implemented |
| Structured operational telemetry | Opt-in JSONL; off by default |
| Safe plan-driven reconciliation (dry-run by default) | Implemented |
| Commercial Collibra tenant validation | Not claimed |

Central safety: sync/apply default to dry-run (zero remote mutations). Writes require `--apply`. Live writes additionally require `--confirm-live`. Impact performs zero remote writes. No automatic deletes. No automatic remediation from impact.

```mermaid
flowchart LR
  PostgreSQL --> Scanner
  ODCS --> OdcsIngest
  dbtManifest --> DbtIngest
  OpenLineage --> OlIngest
  Scanner --> GovernanceGraph
  OdcsIngest --> GovernanceGraph
  DbtIngest --> GovernanceGraph
  OlIngest --> GovernanceGraph
  GovernanceGraph --> Provenance
  GovernanceGraph --> Contracts
  GovernanceGraph --> Lineage
  GovernanceGraph --> Policies
  GovernanceGraph --> ChangeIntelligence
  ChangeIntelligence --> Check
  ChangeIntelligence --> Impact
  ChangeIntelligence --> Plan
  Check --> CliAndPrReview
  Impact --> CliAndPrReview
  Plan --> CliAndPrReview
  CliAndPrReview --> SafeReconciliation
  SafeReconciliation --> CollibraAdapters
```

Neutral core owns the governance graph, provenance, contracts, lineage, policies, and change intelligence. Collibra adapters are the integration boundary for desired-state mapping and plan-driven sync. Plans are built before any write.

## Quick start (clean checkout, mock)

No Collibra tenant is required.

### Bash / Linux / macOS / WSL / Git Bash

```bash
git clone https://github.com/adrianmartnez/collibra-governance-automation.git
cd collibra-governance-automation
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
docker compose up -d --wait
governance scan
governance export
governance diff --mode mock
governance sync --mode mock
governance sync --mode mock --apply
docker compose down -v
```

### PowerShell / Windows

```powershell
git clone https://github.com/adrianmartnez/collibra-governance-automation.git
cd collibra-governance-automation
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
docker compose up -d --wait
governance scan
governance export
governance diff --mode mock
governance sync --mode mock
governance sync --mode mock --apply
docker compose down -v
```

Copy `.env.example` to `.env` only when local overrides are needed. Defaults match the Docker Compose demo. Collibra defaults to mock mode.

Optional Bash helper for the SQL demo contract: `bash sample/verify_demo.sh` (Bash / WSL / Git Bash).

## Impact walkthrough (read-only)

After `pip install -e ".[dev]"`, compose a dbt graph with a `governance-impact-changes` v1 file and write `governance-impact-result` v1:

```bash
# IMPACTED: changed table has a downstream model in the manifest
governance impact \
  --namespace analytics \
  --changes tests/fixtures/github_action/impact/changes.json \
  --dbt-manifest tests/fixtures/github_action/impact/manifest.impacted.json \
  --output impact-result.json
echo $?   # 6 — status=impacted; artifact written; not a failure

# CLEAR: same change, no downstream edge
governance impact \
  --namespace analytics \
  --changes tests/fixtures/github_action/impact/changes.json \
  --dbt-manifest tests/fixtures/github_action/impact/manifest.clear.json \
  --output impact-result.json
echo $?   # 0 — status=clear
```

Inspect `impact-result.json` for `status` and `impact_detected`. Exit `6` means domain result `impacted`, not an operational failure. Analysis performs zero remote writes; source paths are never auto-discovered. The fixtures above are the stable CI inputs already in this repository (not a production dataset).

## CLI

```text
governance scan [--config PATH] [--profile NAME]
governance export [--config PATH] [--profile NAME] [--artifact inventory|snapshot] [--output PATH]
governance diff [--config PATH] [--profile NAME] [--mode mock|live] [--mapping-config PATH] [--json]
governance sync [--config PATH] [--profile NAME] [--mode mock|live] [--mapping-config PATH] [--apply] [--confirm-live] [--json]
governance config validate [--config PATH] [--profile NAME] [--json]
governance check --config PATH [--profile NAME] [--format human|json]
governance plan --config PATH --output FILE.gplan [--profile NAME] [--format human|json] [--odcs PATH ...] [--dbt-manifest PATH ...] [--openlineage PATH ...] [--dbt-default-database NAME]
governance plan inspect FILE.gplan [--format human|json]
governance apply FILE.gplan --config PATH [--profile NAME] [--format human|json] [--apply] [--confirm-live] [--odcs PATH ...] [--dbt-manifest PATH ...] [--openlineage PATH ...] [--dbt-default-database NAME]
governance explain --config PATH --namespace NAME --object-identity PATH [--property POINTER] [--odcs PATH ...] [--dbt-manifest PATH ...] [--openlineage PATH ...] [--dbt-default-database NAME] [--profile NAME] [--format human|json] [--output PATH]
governance compare --baseline PATH --candidate PATH [--align-source-roots] [--align-database-roots] [--format human|json] [--output PATH]
governance drift --comparison PATH [--policy PATH] [--format human|json] [--output PATH]
governance preflight --config PATH [--profile NAME] [--format human|json]
governance impact --namespace NAME --changes FILE --output FILE [--odcs PATH ...] [--dbt-manifest PATH ...] [--openlineage PATH ...] [--dbt-default-database NAME] [--config PATH] [--profile NAME] [--format human|json]
governance --help
governance --version
```

Without `--config`, legacy operational commands keep the v1.0 environment-based settings path. YAML is never auto-discovered from the working directory. New GaC commands `check`, `plan`, `apply`, `explain`, and `preflight` require explicit `--config` (except `plan inspect`).

`governance compare` loads two persisted `governance-snapshot` v1 artifacts and reports material differences offline (no PostgreSQL, Collibra, or `--config`). Difference is not drift: classification of expected vs unexpected differences is out of scope. Optional `--align-source-roots` / `--align-database-roots` acknowledge differing root names for object matching only; root `/name` property differences are still reported. `writes_performed=0` means zero remote governance mutations; optional `--output` may write a local comparison JSON artifact without changing that count.

`governance drift` consumes a persisted `governance-snapshot-comparison` v1 artifact and optionally a `governance-drift-policy` v1 YAML file. When the comparison reports differences, `--policy` is required; missing policy fails explicitly (exit `4`). An explicit empty policy (`rules: []`) is valid and means no differences are permitted. Unexpected drift is reported as data (exit `0`), not as a process failure. Comparison v1 does not include provenance, authority, or conflict payload; drift preserves only context present in the validated comparison input. `writes_performed=0` means zero remote governance mutations.

`governance impact` composes ODCS / dbt / OpenLineage graphs under a shared `--namespace`, reads parent-aware changed nodes from a versioned `governance-impact-changes` v1 file, and writes a canonical `governance-impact-result` v1 artifact. Analysis performs zero remote writes. Source paths are never auto-discovered. Optional `--config` loads configured policies for relevance matching only (not policy evaluation / blocking).

`governance plan` / `governance apply` may optionally take the same explicit ODCS/dbt/OpenLineage source path flags. The reconciliation namespace is the effective PostgreSQL `source_name` from config (no `--namespace` on that path). New plans are `governance-plan` v2 and embed reconciliation assumptions; v1 plans remain loadable and apply only without those source flags. `governance explain` is read-only explainability for authority/conflict decisions (explicit `--namespace`, `--object-identity` JSON GraphNodeIdentity, ≥1 source path required).

## Governance-as-Code (optional)

Declare sources, optional Collibra targets, artifact paths, policy file hooks, and optional metadata authority files in `governance.yaml`. See [`sample/governance.example.yaml`](sample/governance.example.yaml).

```bash
export DATABASE_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/governance_demo}"
export COLLIBRA_MODE="${COLLIBRA_MODE:-mock}"

governance config validate --config sample/governance.example.yaml
governance config validate --config sample/governance.example.yaml --json
governance export --config sample/governance.example.yaml --artifact snapshot

# Policy evaluation (no Collibra I/O)
governance check --config sample/governance.example.yaml
governance check --config sample/governance.example.yaml --format json

# Plan-review-apply (saved .gplan; apply executes the reviewed action set)
governance plan --config sample/governance.example.yaml --output production.gplan
governance plan inspect production.gplan
governance apply production.gplan --config sample/governance.example.yaml
# Mutations still require --apply; live apply still requires --confirm-live
```

Validation covers YAML parse, JSON Schema, profile overlay, and semantic checks before any PostgreSQL or Collibra I/O. Secrets stay in environment variables via `*_env` references. Snapshots are a distinct artifact from the metadata inventory and embed a required `content_identity`. Component identities (`config` / `snapshot` / `mapping` / `policy` / `remote_state` / `target_context` / plan) are integrity digests, not authenticity proofs.

`governance plan` / `governance apply` use `--config` and optional `--profile` as the declarative source of truth (no `--mode` / `--mapping-config` overrides on that path). Legacy `diff` / `sync` keep their existing overrides. YAML and `.gplan` never authorize writes. Remote apply is fail-fast and not a distributed transaction.

Exit codes (legacy commands `scan` / `export` / `diff` / `sync` / `config validate`):

- `0` — completed successfully
- `1` — operational / configuration / integration failure
- `2` — usage / argument / live-apply confirmation error

Additional exit codes for `check` / `plan` / `apply` only:

- `3` — policy error violations (or plan blocked by policy errors)
- `4` — config / runtime-resolution / policy / saved-plan validation failure
- `5` — stale saved plan (apply refused before mutation)

Additional exit code for `impact` only:

- `6` — impact analysis completed with `status=impacted` (artifact written; not a failure)
- `0` — impact analysis completed with `status=clear`
- `4` — impact input / provider / graph / changes validation failure (same meaning as other GaC validation)
- `3` / `5` — unused by `impact` (meanings above unchanged)

Exit codes for `compare` only:

- `0` — comparison completed (`status=identical` or `status=different`; difference is not a failure)
- `2` — usage / argument error
- `4` — snapshot / compatibility / alignment / output validation failure
- `1` / `3` / `5` / `6` — unused by `compare`

Exit codes for `drift` only:

- `0` — drift analysis completed (`status=no_difference`, `expected_difference`, or `unexpected_drift`)
- `2` — usage / argument error
- `4` — comparison / policy / schema / integrity / ambiguity / output validation failure
- `1` / `3` / `5` / `6` — unused by `drift`

`create` / `update` / `unchanged` / `remote_only` counts are plan actions, not completed remote writes. Dry-run means zero remote mutations (`applied=0`); it does not mean zero network in live mode.

### Representative local output (mock demo)

```text
governance 1.3.0
```

```text
source=governance-demo
database=governance_demo
schemas=2
tables=7
columns=41
primary_keys=7
foreign_keys=7
relationships=7
```

```text
mode=mock
create=108
update=0
unchanged=0
remote_only=0
writes=0
CREATE column col:governance-demo/governance_demo/commerce/customers/customer_id
CREATE table tbl:governance-demo/governance_demo/commerce/customers
CREATE relationship rel:fk:tbl:governance-demo/governance_demo/commerce/orders/orders_customer_id_fkey
```

A standalone mock `diff` starts from an empty mock adapter, so the plan commonly lists `CREATE` actions for the desired state. Mock state is process-local and is not persisted between CLI invocations.

```text
mode=mock
dry_run=true
create=108
update=0
unchanged=0
remote_only=0
applied=0
success=true
```

### Metadata authority (v1)

Optional GaC side-files declare which provider is authoritative for a governed property when observations disagree. Reference them from `governance.yaml`:

```yaml
authority:
  files:
    - authority/metadata-authority.example.yaml
```

See [`sample/authority/metadata-authority.example.yaml`](sample/authority/metadata-authority.example.yaml).

Authority document envelope:

```yaml
authority_schema: governance-authority
authority_version: "1"
rules:
  - id: table-description-from-odcs
    description: ODCS owns contractual table descriptions.
    select:
      kind: table
      property: /description
      namespace: production   # optional; omit for global kind+property rules
    authority:
      provider_type: odcs
      source_ref: customer-contract   # optional; omit for provider-only authority
```

Semantics:

- Selector fields:
  - `kind`: exact enum from the authority schema; case-sensitive; no inference or case folding.
  - `property`: strict RFC6901 JSON Pointer; the full pointer is **not** `.strip()`'d; spaces inside segments are material; no URI fragment `#/...`; no percent-decoding.
  - `namespace`: optional; when present, normalized with `.strip()`, case-preserving, exact match after normalization.
- Authority target: `provider_type` and optional `source_ref` are normalized with `.strip()`, case-preserving, exact match; no provider whitelist.
- Specificity: namespace+kind+property (rank 2) beats kind+property (rank 1). The authority target (`provider_type` / optional `source_ref`) does **not** change rank.
- Missing rule ⇒ no guessed winner. Equal-authority conflicting authorized values remain unresolved.
- No first/last/timestamp ordering; credential-bearing fields are rejected by schema.
- Profiles may replace `authority.files` like `policies.files`.
- `config_identity` includes non-empty `authority.files` refs (list order material). `authority_identity` hashes only semantic rule keys; YAML rule ids, descriptions, authority file refs/filenames and formatting are excluded, while selector property paths and authority targets remain material.
- With explicit ODCS/dbt/OpenLineage inputs on `plan` / `apply`, unresolved or invalid authority conflicts that are **relevant** to a planned Collibra mutation block plan generation (exit 4) or cause stale-plan refusal on apply (exit 5) before remote writes. Unrelated conflicts do not block unrelated actions.
- Mapped reconciliation targets: `/name` → `display_name`, `/description`, `/attributes/data_type` (columns), `/attributes/ownership` (database/schema/table). Effective `null` omits on CREATE and preserves the current remote value on existing assets (never Attribute DELETE).
- `governance explain` renders observations, provenance, conflict state, winning rule (when present), and reconciliation safety without remote mutations.

## Mock vs live

### Mock

- Zero external network
- Uses symbolic `mock:*` mapping refs
- No Collibra credentials
- Suitable for Quick Start and CI

### Live

- Uses existing Settings/env auth: exactly one of Basic, caller-supplied Bearer, or OAuth client credentials
- OAuth native Collibra posts to `{base}/rest/oauth/v2/token` (no scope). External IdP uses `COLLIBRA_TOKEN_URL` with `client_secret_post` (default) or `client_secret_basic`
- OAuth token endpoints must be HTTPS or HTTP loopback; HTTP non-loopback is rejected before any token POST
- Requires `--mapping-config PATH` with tenant catalog refs only (no credentials in that file)
- Execution paths: `core_rest` (default), `import_v2` (json-job + poll), `sync_v2` (batched json-job, then IGNORE finalize + poll)
- `diff` and dry-run `sync` may authenticate and perform read-only tenant calls against remote managed state
- Remote mutations require both `--apply` and `--confirm-live`
- Localhost contract HTTP tests are not a commercial-tenant stand-in; commercial tenant validation is not claimed

Example mapping shape: [`sample/collibra-mapping.example.json`](sample/collibra-mapping.example.json). Placeholders such as `<tenant-domain-id>` are intentionally non-functional and are rejected by the live CLI until replaced.

## Live usage

Configure auth in the environment (not on the command line). Use exactly one method:

```text
COLLIBRA_MODE=live
COLLIBRA_BASE_URL=https://your-collibra-host.example
COLLIBRA_USERNAME=...
COLLIBRA_PASSWORD=...
# or COLLIBRA_BEARER_TOKEN=...
# or COLLIBRA_CLIENT_ID=... and COLLIBRA_CLIENT_SECRET=...
# Optional IdP: COLLIBRA_TOKEN_URL=https://idp.example/oauth/token
```

Optional execution and batching:

```text
COLLIBRA_EXECUTION_MODE=core_rest
# COLLIBRA_EXECUTION_MODE=import_v2
# COLLIBRA_EXECUTION_MODE=sync_v2
```

`import_v2` submits `/import/json-job` with `continueOnError=false`, `relationsAction=ADD_OR_IGNORE`, and `attributesAction=REPLACE` only for mapping-managed attribute types. `sync_v2` submits `/import/synchronize/{id}/batch/json-job`, then IGNORE-only `/finalize/job`, and is not success until the **finalization job** is `COMPLETED`+`SUCCESS`. Combined `/import/synchronize/{id}/json-job` is forbidden.

Bounded retries apply to GET 429/5xx and connect failures. Writes retry connect-before-send only. Retry-After supports delta-seconds and HTTP-date.

Read-only tenant checks (zero governance mutations; does not certify write capability). HTTP non-loopback is `INCOMPATIBLE` before credentials:

```bash
governance preflight --config governance.yaml --format json
```

Use a real mapping file derived from the sample placeholders:

```bash
governance diff \
  --mode live \
  --mapping-config tenant-mapping.json

governance sync \
  --mode live \
  --mapping-config tenant-mapping.json

governance sync \
  --mode live \
  --mapping-config tenant-mapping.json \
  --apply \
  --confirm-live
```

Dry-run first. Live dry-run may perform authentication and read-only tenant calls. When OAuth client credentials are configured, authentication may include the OAuth token POST. Dry-run performs zero Collibra governance mutations: it does not submit Core REST mutations, Import jobs, synchronization batches, or finalization jobs.

### Operational telemetry

Telemetry is off by default (`NullSink`). Opt in with `COLLIBRA_TELEMETRY=jsonl` for JSONL on stderr, or set `COLLIBRA_TELEMETRY_PATH` to write a file. Events never mix into CLI stdout JSON.

One correlation ID covers a logical execution: auth, HTTP attempts/retries, pre-execution remote reads, import/sync batches, job polls, and the terminal outcome. `duration_ms` is measured from the root execution scope. Exactly one `execution_outcome` is emitted per logical execution.

Batch events may include batch index/count, bounded workload counts, and `submission_state`. Job events may include `job_id` and normalized state/result. `writes_performed` is included only when known with certainty: `0` on dry-run, `applied_count` on confirmed success, omitted when failed or uncertain.

Endpoint paths are query-free templates. The allowlist excludes Authorization, bearer/access tokens, client secrets, passwords, credential-bearing connection strings, raw request/response bodies, unrestricted query strings, and business-row data. Sink failures never change governance decisions. Correlation, duration, and runtime telemetry never enter plan, snapshot, graph, hash, impact, or apply-result identities.

## PostgreSQL demo

```bash
docker compose up -d --wait
```

Local credentials are fictional and development-only:

| Setting | Value |
| --- | --- |
| Host | `localhost` |
| Port | `5432` |
| Database | `governance_demo` |
| User | `postgres` |
| Password | `postgres` |
| Logical source | `governance-demo` |

Primary demo schema: `commerce` (`customers`, `products`, `employees`, `orders`, `order_items`, `payments`, `marketing_contacts`). Seed data is synthetic (`@example.com`).

## Library seams

The CLI orchestrates the same public layers available to library callers:

```python
from governance.config import load_settings
from governance.exporters import MetadataInventory, write_inventory
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
inventory = MetadataInventory.from_model(model)
write_inventory(inventory, settings.inventory_output_path)

desired = map_to_desired_state(model, mock_mapping_config())
adapter = build_collibra_adapter(settings, mock_mapping_config())
remote = adapter.read_remote_state(desired)
plan = build_sync_plan(desired, remote)
dry_run = execute_sync_plan(adapter, plan, apply=False)
```

## GitHub Action (Governance as Code)

Official composite Action at the repository root (`action.yml`). Supported runners for v1: **GitHub-hosted Linux/Ubuntu** only. Action contract version `v1` is independent of package SemVer `1.3.0`.

The Action installs this package into a fresh Action-owned virtualenv under `RUNNER_TEMP`, then runs the governance CLI with isolated Python (`python -I -m ...`). Consumer site-packages are not modified. Relative config paths resolve against `GITHUB_WORKSPACE` (the consumer must checkout their repository before `uses:`).

**Writes performed: always 0.** The Action never calls `governance apply` or mutating `sync`. Impact is read-only analysis.

### Inputs (v1)

| Input | Default | Notes |
| --- | --- | --- |
| `config` | `""` | Required for `validate`/`check`/`plan` (Phase A). Optional for `impact` (validates config/authority and enables policy matching) |
| `profile` | `""` | Forwarded as `--profile` when non-empty; for impact requires `config` |
| `operation` | `plan` | `validate` \| `check` \| `plan` \| `impact` (Action mode, not Collibra `--mode`) |
| `output-format` | `human` | Console only (`human` \| `json`); does not change artifacts or step summary |
| `fail-on-policy-error` | `"true"` | Final gate exits `3` when status is blocked |
| `output-directory` | `.governance` | Artifact root; must stay inside the workspace |
| `plan-path` | `.governance/governance.gplan` | Must be under `output-directory` |
| `pr-comment` | `"false"` | Opt-in sticky PR comment |
| `github-token` | `""` | Comment step only; never passed to governance CLI |
| `impact-namespace` | `""` | Required for `operation: impact` |
| `impact-changes` | `""` | Workspace-relative `governance-impact-changes` v1 path |
| `impact-odcs` | `""` | JSON array of ODCS paths, e.g. `["contracts/a.yaml"]` |
| `impact-dbt-manifest` | `""` | JSON array of dbt manifest paths |
| `impact-openlineage` | `""` | JSON array of OpenLineage event paths |
| `dbt-default-database` | `""` | Optional default database for dbt loading |

Provider credentials are **not** Action inputs. They remain environment variables referenced by governance.yaml `*_env` keys.

For `operation: impact`, `contract-version` and `result-path` are empty. Use `impact-status`, `impact-result-path`, and `impact-result-version` instead. The machine blast-radius artifact is `governance-impact-result` v1 (`impact-result.json`). Impact is read-only (`writes-performed=0`). CLI exit `6` (`impacted`) is domain success and does not fail the Action by default.

### A. Fork-safe validation

```yaml
name: Governance validate
on: pull_request
permissions:
  contents: read
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: adrianmartnez/collibra-governance-automation@<commit-sha>
        with:
          config: governance.yaml
          operation: validate
          pr-comment: "false"
```

Do not use `pull_request_target` with an untrusted PR checkout and secrets.

### B. Governance check

`operation: check` evaluates policies against a PostgreSQL source. Set `DATABASE_URL` (or the discrete connection env vars from your config). No Collibra I/O is required for check.

### B2. Governance impact (read-only)

```yaml
- id: gac-impact
  uses: adrianmartnez/collibra-governance-automation@<commit-sha>
  with:
    operation: impact
    impact-namespace: analytics
    impact-changes: changes/impact-changes.json
    impact-dbt-manifest: '["dbt/target/manifest.json"]'
    # impact-odcs: '["contracts/orders.yaml"]'
    # impact-openlineage: '["lineage/events.json"]'
    pr-comment: "false"
    output-directory: .governance
```

At least one of `impact-odcs`, `impact-dbt-manifest`, or `impact-openlineage` is required. Source lists are JSON arrays (not comma-separated). Optional `config`/`profile` validate governance configuration (including authority side-files) and enable policy matching (not policy blocking). Upload `${{ steps.gac-impact.outputs.artifacts-path }}` to retain `impact-result.json` and `report.md`.

### C. Trusted deterministic planning

For live remote reads, inject provider env vars only after an explicit same-repository guard:

```yaml
- name: Plan (same repository only)
  if: github.event.pull_request.head.repo.full_name == github.repository
  uses: adrianmartnez/collibra-governance-automation@<commit-sha>
  with:
    config: governance.yaml
    operation: plan
    pr-comment: "false"
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
    COLLIBRA_MODE: mock
    # Live reads: COLLIBRA_BASE_URL / credentials via env refs in governance.yaml
```

Still zero remote writes.

### D. Artifact upload

```yaml
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: governance-action
    path: ${{ steps.gac.outputs.artifacts-path }}
```

Prefer uploading the Action `artifacts-path` directory so a missing `.gplan` on blocked runs does not break the upload. Pin `upload-artifact` by full commit SHA in production workflows.

### E. Optional sticky comment

```yaml
permissions:
  contents: read
  pull-requests: write
# ...
- uses: adrianmartnez/collibra-governance-automation@<commit-sha>
  with:
    config: governance.yaml
    operation: plan
    pr-comment: "true"
    github-token: ${{ github.token }}
```

Fork PRs skip commenting (`comment-status=skipped_untrusted_fork`). Missing token on a trusted PR fails the Action with `comment-status=failed` after governance artifacts are written. The Action uses `GITHUB_API_URL` for GitHub Enterprise Server compatibility.

### F. Version pinning

- Strongest: pin the Action to a full immutable commit SHA.
- Release consumers may pin the immutable SemVer tag `v1.3.0` once that tag is published.
- Prior release tags `v1.2.0` and `v1.1.0` remain available historically.
- Do not use mutable `@main`.
- Package version `1.3.0` ships with Action contract v1 and impact result contracts v1; keep Action/package compatibility explicit across releases.
- The Action ref pins Action metadata and the Python package installed from `GITHUB_ACTION_PATH` together.

## Repository structure

```text
action.yml                       official composite GitHub Action
src/governance/
  domain/                        vendor-neutral model, graph, lineage, impact helpers
  scanner/                       PostgreSQL metadata discovery
  exporters/                     deterministic inventory JSON
  integrations/
    collibra/                    mapping, adapters, import, jobs, batching, preflight, telemetry
    odcs/                        Open Data Contract Standard ingestion + schema
    dbt/                         dbt manifest ingestion
    openlineage/                 OpenLineage event ingestion
  impact/                        impact CLI contracts + impact schemas
  github_ci/                     Action runner, reporting, action-result schema
  config_contract/               governance.yaml schema + resolution
  policy/                        policy schema + evaluation
  plans/                         saved .gplan artifacts
  snapshots/                     governance snapshot artifacts
  identity/                      content-identity hashing
  cli.py                         argparse CLI orchestration
tests/                           unit/integration + fixtures; localhost Collibra contract server
sample/                          demo SQL, GaC example, Collibra mapping example
.github/workflows/               CI quality gates
```

## Testing and CI

```bash
ruff check src tests
ruff format --check src tests
pytest -m "not integration and not collibra_integration and not cli_integration and not collibra_contract"
python -m build
```

Contract tests (loopback HTTP only; no commercial tenant):

```bash
pytest -m collibra_contract
```

With the demo database running:

```bash
pytest -m integration
pytest -m collibra_integration
pytest -m cli_integration
docker compose down -v
```

CI defines eight `ubuntu-latest` jobs:

- `lint`
- `unit-tests`
- `collibra-contract` (dedicated gate; loopback only)
- `package-validation`
- `postgres-integration`
- `metadata-integration`
- `collibra-integration`
- `cli-integration` (includes official Action `uses: ./` PASS, BLOCKED, impact CLEAR/IMPACTED/ERROR smokes)

No commercial Collibra tenant, self-hosted runners, or OS matrix is required. The localhost contract server is not a commercial-tenant stand-in.

## Limitations

- No commercial Collibra tenant validation
- Local contract-server coverage is not commercial-tenant validation
- No automatic deletes or destructive reconciliation
- No automatic apply/remediation from impact analysis
- No plan/apply blocking on unresolved property conflicts yet
- No arbitrary tenant customization beyond configured refs
- No transactional REST snapshot across concurrent mutations
- No large-scale performance benchmark
- Mock state is process-local demonstration state
- Demo data is fictional
- This package remains a technical governance automation project, not a hosted governance platform

## License

[MIT](LICENSE)

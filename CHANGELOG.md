# Changelog

## [Unreleased]

## 1.4.0 - 2026-09-03

### Added

- Property-level metadata observations with provenance (`governance-property-observations` v1)
- Deterministic metadata authority policies (`governance-authority` v1)
- Deterministic property conflict analysis with effective-value derivation and explicit resolved/unresolved states
- Unsafe reconciliation blocking when unresolved or ambiguous mapped conflicts are relevant to planned mutations (zero-write fail-safe)
- Read-only `governance explain` workflow/result for authority and conflict decisions
- Offline snapshot comparison (`governance-snapshot-comparison` v1) with optional root-alignment acknowledgements
- Expected vs unexpected governance drift classification (`governance-drift-policy` / `governance-drift-result` v1)
- Deterministic local governance history with optional observations/authority context (`governance-history` / evolution contracts)
- Official GitHub Action `operation: review` for conflict/authority/drift PR surfacing (`governance-ci-review-result` v1): bounded secret-safe reports with full machine detail retained in artifacts

### Safety

- Authority is explicit; no last-write-wins or first-write-wins selection
- Unresolved or invalid mapped conflicts that are applicable to planned Collibra mutations block before remote writes
- `explain` / `compare` / `drift` / `history` / Action `review` perform zero remote governance mutations
- Action review never mutates Collibra; GitHub token is isolated from review orchestration
- Public report/summary/outputs redact governed values and `source_ref` where required; full detail stays in artifacts
- Dry-run-by-default remains; live writes still require `--apply` and `--confirm-live`
- No automatic delete or destructive reconciliation

### Compatibility

- Package and runtime version are now `1.4.0` (distinct from machine contract versions, which remain v1 where defined)
- Existing supported v1.0–v1.3 flows are preserved where designed
- Action `validate` / `check` / `plan` semantics unchanged; impact semantics unchanged
- Existing public v1 contracts are not silently revved; v1.4 contracts were introduced additively in their feature PRs

### Limitations

- No commercial Collibra tenant validation in this repository
- Localhost contract-server tests are not a commercial-tenant stand-in
- No provider SDK
- No hosted governance service
- No automatic destructive reconciliation
- No claim of a transactional remote-state snapshot across concurrent mutations
- No large-scale performance certification
- This package remains a technical governance automation project, not a hosted governance platform

## 1.3.0 - 2026-08-19

### Added

- OAuth 2.0 client-credentials for live Collibra: native `POST {base}/rest/oauth/v2/token` and external IdP (`client_secret_post` / `client_secret_basic`)
- Bounded HTTP retry policy with Retry-After delta-seconds and HTTP-date
- Plan-faithful Import API v2 `json-job` execution (`core_rest` | `import_v2` | `sync_v2`)
- Documented job lifecycle and IGNORE-only `sync_v2` finalization, including poll of the finalization job
- Conservative deterministic batching with stop-on-failure (config may only lower the hard maxima)
- Localhost Collibra contract HTTP server and dedicated `collibra-contract` CI job
- Read-only `governance preflight` tenant compatibility CLI
- Secret-safe operational telemetry (`NullSink` default; `COLLIBRA_TELEMETRY=jsonl` opt-in): one logical correlation across auth, HTTP retries, batches, and jobs; root `duration_ms`; exactly one terminal `execution_outcome`; `writes_performed` only when known with certainty

### Safety

- Live auth remains XOR: Basic, caller-supplied Bearer, or OAuth client credentials
- OAuth HTTP non-loopback is rejected before any token POST; embedded URL credentials are rejected
- Import `attributesAction=REPLACE` is limited to mapping-managed attribute types; relations use `ADD_OR_IGNORE`
- Combined `/import/synchronize/{id}/json-job`, `REMOVE_RESOURCES`, `CHANGE_STATUS`, and DELETE remain forbidden
- Preflight performs zero remote mutations; remote HTTP is classified incompatible before credentials are sent
- Telemetry is allowlisted and fail-open: dynamic endpoint IDs and query strings are not exposed; secrets, raw payloads, and business-row data are excluded; sink failures cannot alter governance decisions; telemetry stays outside plan, snapshot, graph, hash, impact, and apply-result identities

### Compatibility

- Public v1 contracts unchanged (`schema_version`, `plan_version`, Action contract, hashing contract)
- Existing Basic/Bearer configs without OAuth or `execution_mode` keep 1.2.0 runtime behavior
- Package and runtime version are now `1.3.0` (distinct from contract versions, which remain v1)

### Limitations

- No commercial Collibra tenant validation in this repository
- Local contract-server tests are not a commercial-tenant stand-in
- No provider SDK
- No authority / conflict resolution engine
- No automatic destructive reconciliation
- No large-scale performance benchmarks
- This package remains a technical governance automation project, not a hosted governance platform

## 1.2.0 - 2026-08-09

### Added

- Vendor-neutral governance graph and provenance foundation for contracts, lineage, and related assets
- Open Data Contract Standard (ODCS) document ingestion into the governance graph
- dbt manifest metadata ingestion with dependency edges
- OpenLineage event and dataset facet ingestion
- Deterministic column-level lineage representation
- Downstream traversal and governance blast-radius analysis
- Versioned deterministic `governance-impact-changes` v1 and `governance-impact-result` v1 artifacts
- `governance impact` CLI for read-only impact analysis with optional policy relevance matching
- Official GitHub Action `operation: impact` with CLEAR / IMPACTED / FAILED semantics
- Bounded deterministic PR / `GITHUB_STEP_SUMMARY` impact reports and explainable downstream paths
- Real composite Action smokes for impact CLEAR / IMPACTED / ERROR paths

### Safety

- Impact analysis performs zero remote writes (`writes_performed=0`); no automatic apply or remediation
- Source paths for ODCS / dbt / OpenLineage are never auto-discovered
- Provider credentials remain environment references; they are not Action inputs
- Fork-safe / least-privilege Action defaults preserved for impact and existing operations
- Fail-last materialization of Action artifacts; CLI exit `6` (`impacted`) is domain success, not failure
- Existing dry-run-by-default sync/apply controls and no-automatic-delete policy unchanged

### Compatibility

- Existing `validate` / `check` / `plan` Action operations and CLI behavior preserved
- `governance-action-result` v1 unchanged
- Package and runtime version are now `1.2.0` (distinct from contract versions, which remain v1)
- Existing v1.0 / v1.1 workflows remain compatible where designed

### Limitations

- No commercial Collibra tenant validation in this repository
- No production-scale Collibra provider hardening
- No provider SDK
- No authority / conflict resolution engine
- No automatic destructive reconciliation
- No large-scale performance benchmarks
- This package remains a technical governance automation project, not a hosted governance platform

## 1.1.0 - 2026-08-08

### Added

- Declarative `governance.yaml` v1 configuration with packaged JSON Schema, profile overlays, diagnostics, and `governance config validate`
- Deterministic `GovernanceSnapshot` artifact (`export --artifact snapshot`), distinct from the v1.0 metadata inventory
- Versioned content identities (`config_identity`, `snapshot_identity`, `mapping_identity`) using SHA-256 with domain separation
- Native deterministic policy evaluation via `governance check`, packaged policy schema, `policy_identity`, and machine-readable policy reports
- Saved governance plan artifacts (`.gplan`) via `governance plan` / `governance plan inspect` / `governance apply`
- Stale-plan protection with material input identities including `target_context_identity` and `remote_state_identity`
- Official root composite GitHub Action (`action.yml`) for read-only Governance-as-Code review workflows (`validate` / `check` / `plan`)
- Packaged `governance-action-result` v1 machine contract and Action orchestration under `governance.github_ci`
- Deterministic Markdown PR reports, `GITHUB_STEP_SUMMARY`, and bounded workflow annotations for CI review
- Opt-in sticky pull request comments with fork-safe defaults and least-privilege permissions
- Sample config at `sample/governance.example.yaml` (secrets remain environment references only)
- Additive exit codes for Governance-as-Code commands: policy failure (`3`), config/resolution/artifact validation (`4`), stale plan (`5`)

### Safety

- Official GitHub Action operations (`validate` / `check` / `plan`) perform zero remote writes (`writes_performed=0`)
- Remote mutation remains explicit via `governance apply` (and existing sync `--apply` / `--confirm-live` controls)
- Saved-plan apply validates staleness before any write
- Fork and untrusted PR paths do not receive privileged sticky-comment delivery or provider credentials through Action inputs
- Provider credentials remain environment references from `governance.yaml`; they are not Action inputs
- No automatic remote deletes or destructive reconciliation

### Limitations

- No commercial Collibra tenant validation in this repository
- No automatic destructive reconciliation
- GitHub Action v1 is supported on GitHub-hosted Linux/Ubuntu runners
- Collibra remote reads are not treated as a transactional snapshot
- No large-scale performance benchmarks
- Sticky pull request comments are opt-in
- This package remains a technical governance automation project, not a hosted governance platform

## 1.0.0 - 2026-08-07

### Added

- PostgreSQL technical metadata discovery from system catalogs into a vendor-neutral governance model
- Deterministic, versioned metadata inventory JSON export
- Collibra-oriented asset and relationship mapping with configuration-driven type refs
- Mock Collibra adapter for local offline demonstration (process-local state)
- Live Collibra Core REST API v2 adapter boundary (contract-tested)
- Plan-driven metadata diff and safe synchronization (dry-run by default, no automatic deletes)
- End-to-end `governance` CLI: `scan`, `export`, `diff`, `sync`
- Focused automated quality gates: lint, unit tests, package validation, PostgreSQL demo, metadata integration, Collibra mock lifecycle, and CLI integration

### Safety

- Sync defaults to dry-run (`applied=0`); writes require `--apply`
- Live writes additionally require `--confirm-live`
- Managed-only reconciliation; unmanaged tenant objects are ignored
- Mapping config carries catalog refs only; authentication remains in environment settings

### Limitations

- No commercial Collibra tenant validation in this repository
- No OAuth token acquisition or refresh
- No automatic remote deletes or destructive reconciliation
- No arbitrary tenant customization beyond configured type refs
- Collibra REST reads are not treated as a transactional snapshot
- No large-scale performance benchmarks
- Mock adapter state is process-local demonstration state
- Demo dataset is fictional

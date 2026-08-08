# Changelog

## [Unreleased]

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

## 1.0.0

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

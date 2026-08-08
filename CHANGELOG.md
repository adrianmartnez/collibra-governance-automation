# Changelog

## [Unreleased]

### Added

- Governance-as-Code foundation: optional `governance.yaml` v1 contract with packaged JSON Schema, profile overlays, and `governance config validate`
- Canonical `GovernanceSnapshot` artifact (`export --artifact snapshot`) distinct from the v1.0 metadata inventory
- Versioned content identities (`config_identity`, `snapshot_identity`, `mapping_identity`) using SHA-256 with domain separation
- Sample config at `sample/governance.example.yaml` (secrets remain environment references only)

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

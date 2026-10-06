# Migration guide: v1.4 → v2

- `governance.yaml` v1 remains supported.
- Legacy CLI/Action source flags remain supported.
- v2 is additive: select providers by stable `provider_id`.
- Secrets use `$env` references; core resolves env before factories.
- Collibra v2 mapping is inline; ODCS/dbt/OpenLineage paths are config-root-relative.
- Third-party providers require explicit installation (CLI env or Action `runtime-python`).
- Duplicate `provider_id` values hard-fail (no precedence).
- Provider SDK API remains `"1"`; package SemVer stays `1.4.0` until the v2.0.0 release PR.
- No silent removal of legacy Action inputs in this release train.

# Migration guide: v1.4 → v2

Package SemVer moves to `2.0.0`. Provider SDK API remains `"1"`. Machine contracts are
not silently revved by the package major.

Migration to `governance.yaml` v2 and to third-party providers is **optional**. Existing
v1 configs and legacy CLI/Action source flags remain supported.

## What stays the same

- `governance.yaml` v1 continues to load and run on supported paths.
- Legacy CLI flags such as `--odcs`, `--dbt-manifest`, and `--openlineage` remain supported.
- Legacy Action impact source inputs remain supported when `impact-sources-from-config` is
  `false` (the default).
- Dry-run-by-default, plan-before-apply, stale-plan protection, conflict zero-write blockers,
  and no automatic destructive reconciliation remain intact.
- Provider SDK API stays `"1"`.

## What is new in v2.0

- Public Provider SDK (`governance.providers`) with eight frozen capabilities.
- Built-in providers register through the same public model as third parties.
- Additive `governance.yaml` schema v2 with provider-neutral sources/targets and `$env`.
- Public conformance kit (`governance.conformance`).
- Companion third-party proof/template: `example.catalog`.
- Additive Action inputs `runtime-python` and `impact-sources-from-config`.

## Version axes

| Axis | v1.4.0 | v2.0.0 |
| --- | --- | --- |
| Package / runtime | `1.4.0` | `2.0.0` |
| Provider SDK API | (none published) | `"1"` |
| Machine contracts | v1 where defined | unchanged unless deliberately versioned |
| Provider package version | n/a | PEP 440 per distribution |

## v1 → v2 configuration example

### v1 (`*_env`, nested connection, mapping path)

```yaml
schema_version: "1"
sources:
  - id: primary
    provider: postgresql
    config:
      source_name: governance-demo
      connection:
        database_url_env: DATABASE_URL
targets:
  - id: collibra
    provider: collibra
    config:
      mode_env: COLLIBRA_MODE
      mapping:
        path: collibra-mapping.example.json
      auth:
        base_url_env: COLLIBRA_BASE_URL
        password_env: COLLIBRA_PASSWORD
```

### v2 (flat provider config, `$env`, inline Collibra mapping)

```yaml
schema_version: "2"
sources:
  - id: primary
    provider: postgresql
    config:
      source_name: governance-demo
      database_url:
        $env: DATABASE_URL
targets:
  - id: collibra
    provider: collibra
    config:
      mode: mock
      mapping:
        domain_ref: "<tenant-domain-id>"
        asset_type_refs: { ... }
        relation_type_refs: { ... }
        attribute_type_refs: { ... }
```

See [`sample/governance.example.yaml`](../../sample/governance.example.yaml) (v1) and
[`sample/governance.v2.example.yaml`](../../sample/governance.v2.example.yaml) (v2).

## Sources, targets, and provider IDs

- Select providers by stable `provider_id` (`postgresql`, `odcs`, `dbt`, `openlineage`,
  `collibra`, or a third-party id such as `example.catalog`).
- Logical `id` values must be unique within `sources` and within `targets` (the same
  string may appear once in each collection).
- Duplicate installed `provider_id` values hard-fail (no precedence by import order).

## `$env`

- v2 secrets and connection material use `{"$env": "VAR_NAME"}` (YAML map form).
- Core resolves `$env` during `resolve_provider_configuration` **before** operational I/O.
- Missing environment variables fail closed at resolution time (including
  `governance config validate` for v2).

## File-provider paths

- ODCS / dbt / OpenLineage `path` values are relative to the governance.yaml directory
  (config root). Paths must not escape via `..`.

## Collibra mapping: v1 vs v2

- **v1:** `mapping.path` points at a JSON file (see
  [`sample/collibra-mapping.example.json`](../../sample/collibra-mapping.example.json)).
- **v2:** `mapping` is an **inline object** with the same semantic keys
  (`domain_ref`, `asset_type_refs`, `relation_type_refs`, `attribute_type_refs`).
  There is no `mapping.path` in the v2 Collibra provider config.

## Third-party installation

- Install the provider into the same Python environment as the core (CLI) or into a
  caller-prepared Action runtime (`runtime-python`).
- The Action installs **only** the core package; it never installs providers.
- Guaranteed GitHub-only install path after the `v2.0.0` tag:

  ```text
  pip install "collibra-governance-automation @ git+https://github.com/adrianmartnez/collibra-governance-automation.git@v2.0.0"
  ```

- Do not assume PyPI availability unless the package is actually published there.
  See [packaging.md](packaging.md).

## Action `runtime-python`

- Empty (default): Action creates an isolated venv and installs only core.
- Set: workspace-relative path to a Python interpreter that already contains trusted
  providers; Action installs core only into that interpreter.
- Combine with `impact-sources-from-config: "true"` for config-driven graph sources.

## Checklist

- [ ] Decide whether to keep v1 yaml or migrate selected workflows to v2.
- [ ] Convert secrets to `$env` if using v2.
- [ ] Convert Collibra mapping path → inline object if using v2 targets.
- [ ] Install any third-party providers into the CLI env or Action `runtime-python`.
- [ ] Run full conformance for every declared capability.
- [ ] Confirm package version `2.0.0` and SDK API `"1"` on the host.

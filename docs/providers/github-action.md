# GitHub Action — provider-neutral usage

## New inputs (additive)

### `runtime-python` (default `""`)

Empty: Action creates an isolated venv and installs only this Action/core package
(legacy behavior).

Set to a workspace-relative Python interpreter prepared by the workflow owner that
already contains trusted providers. The Action installs **only** the core/Action
package into that interpreter. It never installs providers.

Constraints enforced by the Action bootstrap:

- path must be workspace-relative (no `..`, no absolute paths)
- path must exist, be a file, and be executable
- interpreter must be Python 3.12+

### `impact-sources-from-config` (default `false`)

`false`: legacy Phase-A — impact requires ≥1 `impact-odcs` / `impact-dbt-manifest` /
`impact-openlineage` input.

`true`: requires `config`; allows zero legacy source flags so `governance.yaml` v2 can
select `governance_graph` providers. Legacy flags may still be combined with config-driven
sources. Selection is capability-based (no provider-name branching).

## Example

```yaml
- name: Prepare trusted provider runtime
  run: |
    python -m venv .governance-runtime
    .governance-runtime/bin/python -m pip install ./my-provider

- uses: adrianmartnez/collibra-governance-automation@...
  with:
    runtime-python: .governance-runtime/bin/python
    config: governance.yaml
    operation: impact
    impact-sources-from-config: "true"
    impact-namespace: demo
    impact-changes: changes.json
```

Non-goals: `provider-package`, remote URLs, automatic provider installation.

See also [author-guide.md](author-guide.md) and [packaging.md](packaging.md).

# GitHub Action — provider-neutral usage

## New inputs (additive)

### `runtime-python` (default `""`)

Empty: Action creates an isolated venv and installs only this Action/core package
(legacy behavior).

Set to a workspace-relative Python interpreter prepared by the workflow owner that
already contains trusted providers. The Action installs **only** the core/Action
package into that interpreter. It never installs providers.

### `impact-sources-from-config` (default `false`)

`false`: legacy Phase-A — impact requires ≥1 `impact-odcs` / `impact-dbt-manifest` /
`impact-openlineage` input.

`true`: requires `config`; allows zero legacy source flags so `governance.yaml` v2 can
select `governance_graph` providers. Legacy flags may still be combined.

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

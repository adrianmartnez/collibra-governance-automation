# Provider author guide

Build an independent Python distribution that registers through `governance.providers`
and uses only public APIs (`governance.providers`, `governance.domain`,
`governance.conformance`).

```python
from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRuntimeContext,
)
from governance.domain import GovernanceGraph, PropertyObservationSet
```

## Steps

1. **Stable `provider_id`.** Match
   `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$` (lowercase dotted id; invalid IDs are
   rejected — no silent lowercasing). See [sdk-reference.md](sdk-reference.md).

2. **Truthful capabilities.** Declare an explicit subset of the frozen eight
   capability IDs. No capability implies another. See [capabilities.md](capabilities.md).

3. **Factories accept `ProviderRuntimeContext`.** Capability factories are not
   zero-argument. `context.config` is the resolved bounded payload;
   `context.config_root` is the governance.yaml directory. Secret values in
   `config` are omitted from `repr`.

4. **Optional config validator.** Implement `ProviderConfigValidator.validate(config)`
   for the **unresolved** bounded payload during `resolve_provider_configuration`
   (before core `$env` resolution). It must not merge profiles, resolve secrets,
   or authorize mutation.

5. **Zero-argument `register()`.** Export `register() -> ProviderRegistration`.
   Registration and discovery never invoke factories and never perform operational I/O.

6. **Entry point.** Declare group `governance.providers` in `pyproject.toml`:

   ```toml
   [project.entry-points."governance.providers"]
   catalog = "my_package.provider:register"
   ```

7. **Full conformance.** Run `run_provider_conformance` with a scenario for
   **every** declared capability. Partial suite ≠ full conformance.
   See [conformance.md](conformance.md).

8. **Consume via `governance.yaml` v2** plus CLI/Action without editing core source.
   Select providers by stable `provider_id`. Secrets use `$env` refs.

### Minimal `register()` sketch

```python
def register() -> ProviderRegistration:
    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="acme.catalog",
            display_name="Acme Catalog",
            provider_version="1.0.0",
            sdk_compatibility=">=1,<2",
            capabilities=(CapabilityId.GOVERNANCE_GRAPH,),
        ),
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.GOVERNANCE_GRAPH,
                factory=build_graph_capability,
            ),
        ),
    )


def build_graph_capability(context: ProviderRuntimeContext):
    ...
```

## GitHub Action (`runtime-python`)

The official Action installs **only** the core package. It never installs provider
packages.

1. Prepare a trusted runtime (venv) and install your provider into it.
2. Pass `runtime-python` as a workspace-relative path to that interpreter.
3. For config-driven impact sources, set `impact-sources-from-config: "true"` with
   a `governance.yaml` v2 that selects `governance_graph` providers.

See [github-action.md](github-action.md) and [packaging.md](packaging.md).

## Template

https://github.com/adrianmartnez/governance-provider-example

## Do not

Do not import `governance.providers.builtins`, `governance.integrations`, or
`governance.orchestration` from third-party providers.

# Provider SDK public reference

Public Python surface for third-party and future built-in providers.

**Architecture contract:** [provider-sdk-v2.md](../architecture/provider-sdk-v2.md)  
**SDK API version:** `1`  
**Entry-point group:** `governance.providers`  
**Package version:** remains independent (`1.4.0` at foundation landing)

Built-in integrations (PostgreSQL, ODCS, dbt, OpenLineage, Collibra) are **not** yet registered through this SDK. Existing CLI/Action behavior is unchanged.

## Import surface

```python
from governance.providers import (
    PROVIDER_ENTRY_POINT_GROUP,
    PROVIDER_SDK_API_VERSION,
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRuntimeContext,
    discover_providers,
)
```

`governance.providers.__all__` is the intentional public API. Do not import private helpers from submodule internals unless they are re-exported there.

## Descriptor

`ProviderDescriptor` fields:

- `provider_id` — lowercase dotted id (`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`)
- `display_name`
- `provider_version` — PEP 440
- `sdk_compatibility` — PEP 440 specifier set (syntax validated on construction)
- `capabilities` — explicit subset of known capability IDs

Invalid IDs are rejected (no silent lowercasing). A specifier such as `>=2` is structurally valid even when incompatible with SDK API `1`.

## Capabilities

| ID | Role |
| --- | --- |
| `metadata_discovery` | source |
| `governance_graph` | source |
| `property_observations` | source |
| `lineage` | source |
| `remote_state_read` | target |
| `target_planning` | target |
| `compatibility_preflight` | target |
| `authorized_mutation` | target (execution only; never authorization) |

No capability implies another. Negotiation is explicit via `ProviderRegistry.require_capabilities`.

## Registration and factories

Entry points must expose a **zero-argument** callable returning `ProviderRegistration`.

Capability factories are **not** zero-argument:

```python
def factory(context: ProviderRuntimeContext) -> SomeCapability:
    ...
```

`ProviderRuntimeContext.config` is the bounded provider payload only. Registration and discovery never invoke factories and never perform operational I/O.

Optional `ProviderConfigValidator.validate(config) -> None` validates that bounded payload later (#93/#94). It must not merge profiles, resolve secrets, or authorize mutation.

## Registry

```python
registry = ProviderRegistry()
registry.register(registration)
provider = registry.get("acme.example")
registry.require_capabilities("acme.example", [CapabilityId.LINEAGE])
for item in registry.list_providers():  # lexicographic by provider_id
    ...
```

Duplicate `provider_id` values are hard errors. Unknown providers and missing capabilities fail closed. SDK membership (`"1" in SpecifierSet(...)`) is enforced at registry registration time.

## Discovery

```python
registry = discover_providers()
provider = registry.get("example.fixture")
```

Uses `importlib.metadata` entry points in group `governance.providers`. Broken entry points produce secret-safe diagnostics (exception type, not raw exception strings). Discovery is not wired into the CLI; `governance --help` does not initialize providers.

## Entry-point example

```toml
[project.entry-points."governance.providers"]
example = "my_package.provider:register"
```

```python
def register() -> ProviderRegistration:
    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="acme.example",
            display_name="Acme Example",
            provider_version="1.0.0",
            sdk_compatibility=">=1,<2",
            capabilities=(CapabilityId.LINEAGE,),
        ),
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.LINEAGE,
                factory=build_lineage_capability,
            ),
        ),
    )
```

## Compatibility axes

| Axis | Example | Coupled to SDK API? |
| --- | --- | --- |
| Provider SDK API | `"1"` | — |
| Package SemVer | `1.4.0` | no |
| Provider package version | PEP 440 | no |
| Machine contracts | snapshot/plan/… v1 | no |

## Trust boundary

Installed providers are trusted Python dependencies. There is no sandbox. Conformance (future) is not a security audit, vendor certification, or production certification.

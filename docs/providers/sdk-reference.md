# Provider SDK public reference

Public Python surface for third-party and future built-in providers.

**Architecture contract:** [provider-sdk-v2.md](../architecture/provider-sdk-v2.md)  
**SDK API version:** `1`  
**Entry-point group:** `governance.providers`  
**Package version:** remains independent (`1.4.0` at foundation landing)

Public surfaces for Provider SDK API `1`:

| Module | Role |
| --- | --- |
| `governance.providers` | Provider SDK |
| `governance.domain` | Vendor-neutral domain companion API |
| `governance.conformance` | Conformance / test API |

Built-in providers (PostgreSQL, ODCS, dbt, OpenLineage, Collibra) register through the same
`ProviderRegistry` as third-party entry points. v1 CLI/Action paths that use `Settings`
resolution are unchanged until explicitly migrated.

Author docs index: [README.md](README.md). Conformance: [conformance.md](conformance.md).
Template: https://github.com/adrianmartnez/governance-provider-example


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

### Target capability contracts

- `remote_state_read` produces remote state (`RemoteStateReadCapability.read_remote_state(request)`).
- `target_planning` consumes **desired state + remote state** and produces a plan
  (`TargetPlanningCapability.build_plan(desired_state, remote_state)`).
- Planning MUST NOT mutate remote state and MUST NOT perform a second remote read.
- `authorized_mutation` executes work already authorized by the core; it never authorizes itself.

Generic TypeVars on these protocols use PEP 484 variance (`_co` / `_contra`) appropriate to
input-only vs output-only positions.

## Registration and factories

Entry points must expose a **zero-argument** callable returning `ProviderRegistration`.

Capability factories are **not** zero-argument:

```python
def factory(context: ProviderRuntimeContext) -> SomeCapability:
    ...
```

`ProviderRuntimeContext` carries the resolved bounded provider payload in `.config` and the governance.yaml directory in `.config_root`. The `config` field is omitted from `repr` so resolved secret values are not leaked. Registration and discovery never invoke factories and never perform operational I/O.

Optional `ProviderConfigValidator.validate(config) -> None` validates the **unresolved** bounded payload during `resolve_provider_configuration` (before core `$env` resolution). It must not merge profiles, resolve secrets, or authorize mutation. Capability factories run only via explicit `construct_provider_capability` after resolution.

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

Installed providers are trusted Python dependencies. There is no sandbox. Conformance
validates cooperative observable behavior; it is not a security audit, vendor
certification, or production certification. See [trust-model.md](trust-model.md) and
[conformance.md](conformance.md).

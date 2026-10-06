# Provider author guide

Build an independent Python distribution that registers through `governance.providers` and uses only public APIs.

`python
from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRuntimeContext,
)
from governance.domain import GovernanceGraph, PropertyObservationSet
`

## Steps

1. Choose a stable provider_id matching the documented regex.
2. Declare truthful capabilities from the frozen set of eight.
3. Implement factories that accept ProviderRuntimeContext.
4. Optionally implement ProviderConfigValidator for unresolved config.
5. Export a zero-argument register() -> ProviderRegistration entry point.
6. Declare entry point group governance.providers in pyproject.toml.
7. Run full conformance with a scenario for every declared capability.
8. Consume via governance.yaml v2 + CLI/Action without editing core source.

Template: https://github.com/adrianmartnez/governance-provider-example

Do not import governance.providers.builtins, governance.integrations, or governance.orchestration.

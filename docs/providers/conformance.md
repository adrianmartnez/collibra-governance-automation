# Provider conformance

Import:

```python
from governance.conformance import (
    GraphCase,
    ObservationsCase,
    RegistrationCase,
    assert_conformance,
    empty_runtime_context,
    run_provider_conformance,
    run_source_capability_conformance,
    run_target_capability_conformance,
)
```

External repositories (including the companion template) typically run the full suite from
their own CI after installing a host core that provides Provider SDK API `"1"`.

## Full vs partial

- `run_provider_conformance(...)` requires a scenario for **every** declared capability.
  Missing scenarios fail with check id `missing_conformance_scenario`.
- `run_source_capability_conformance` / `run_target_capability_conformance` may run a
  deliberate subset. **Partial suite != full provider conformance.**

## RegistrationCase

```python
RegistrationCase(register=register)  # Callable[[], ProviderRegistration]
```

Calls `register()` twice and compares contractual fields only (not callable/object identity).
The harness does not invoke capability factories.
Conformance does **not** certify absence of arbitrary filesystem/network/subprocess I/O
during `register()` — that remains a trust-model obligation.

## Truthfulness

For each declared capability with a scenario: binding → factory(context) → Protocol
operation → contractual result validation. No `hasattr` duck-sniffing substitute.

## Failures

`assert_conformance(report)` raises dedicated `ConformanceFailure(Exception)` (not a
`ProviderError` subclass) when checks fail. No new machine CLI artifact contract.

## What conformance does not certify

Not a security sandbox, malicious-code audit, vendor certification, production
certification, performance certification, or Collibra tenant certification.

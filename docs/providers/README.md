# Provider documentation index

**Package SemVer:** `2.0.0` · **Provider SDK API:** `"1"`

Public surfaces for third-party provider authors:

| Surface | Role |
| --- | --- |
| `governance.providers` | Provider SDK (descriptor, registration, discovery, capabilities) |
| `governance.domain` | Vendor-neutral domain companion API (graphs, observations, models, lineage) |
| `governance.conformance` | Test/conformance API (not a runtime dependency of pytest) |

Normative architecture (frozen in #89; implemented in #90–#101; release-prepared in #102
without redefining semantics):
[../architecture/provider-sdk-v2.md](../architecture/provider-sdk-v2.md)

## Read first

1. [Author guide](author-guide.md)
2. [SDK reference](sdk-reference.md)
3. [Conformance](conformance.md)
4. [Packaging](packaging.md)

## Guides

- [SDK reference](sdk-reference.md)
- [Author guide](author-guide.md)
- [Capabilities](capabilities.md)
- [Conformance](conformance.md)
- [Trust model](trust-model.md)
- [Packaging](packaging.md)
- [GitHub Action](github-action.md)
- [Migration v1.4 → v2](migration-v1.4-to-v2.md)

## Companion template

Authoritative example/template repository:

https://github.com/adrianmartnez/governance-provider-example

Core CI pins a bounded snapshot under `tests/fixtures/providers/governance_provider_example/`
with `UPSTREAM_REPOSITORY` / `UPSTREAM_COMMIT` metadata.

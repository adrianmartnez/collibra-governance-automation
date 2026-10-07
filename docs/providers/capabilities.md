# Capability reference

Frozen capability IDs (Provider SDK API `"1"`):

| ID | Role | Protocol operation |
| --- | --- | --- |
| `metadata_discovery` | source | `discover() -> GovernanceModel` |
| `governance_graph` | source | `load_graph() -> GovernanceGraph` |
| `property_observations` | source | `load_observations() -> PropertyObservationSet` |
| `lineage` | source | `load_lineage() -> Sequence[ColumnLineageAssertion]` |
| `remote_state_read` | target | `read_remote_state(request)` |
| `target_planning` | target | `build_plan(desired_state, remote_state)` |
| `compatibility_preflight` | target | `run_preflight()` |
| `authorized_mutation` | target | `execute_authorized(request)` |

No capability implies another. Advertisements must be truthful.
`authorized_mutation` never authorizes; the core delivers already-authorized work.

Planning MUST NOT mutate remote state and MUST NOT perform a second remote read.
See [sdk-reference.md](sdk-reference.md) and [conformance.md](conformance.md).

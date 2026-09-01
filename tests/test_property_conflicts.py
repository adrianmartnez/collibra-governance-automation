"""Unit tests for property conflict analysis."""

from __future__ import annotations

from governance.domain.authority import (
    AuthorityDeclaration,
    AuthorityRuleKey,
    AuthoritySelector,
    AuthorityTarget,
    NormalizedAuthorityPolicySet,
    NormalizedAuthorityRule,
)
from governance.domain.conflicts import analyze_property_conflicts
from governance.domain.graph import (
    NODE_KIND_TABLE,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationSet,
    PropertyPath,
)

NS = "acme.commerce"
PATH_DESC = PropertyPath(("description",))


def _identity(logical_id: str = "orders") -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, NODE_KIND_TABLE, logical_id)


def _prov(
    provider: str,
    ref: str,
    version: str | None = "1.0",
    mode: str = "observed",
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type=provider,
        source_ref=ref,
        source_version=version,
        observation_mode=mode,
    )


def _obs(
    value: object,
    *provenances: ProvenanceRecord,
    identity: GraphNodeIdentity | None = None,
) -> PropertyObservation:
    return PropertyObservation(
        object_identity=identity or _identity(),
        property_path=PATH_DESC,
        value=value,
        provenance=provenances,
    )


def _rule(
    provider: str,
    *,
    source_ref: str | None = None,
    namespace: str | None = None,
    rule_id: str = "rule",
) -> NormalizedAuthorityRule:
    return NormalizedAuthorityRule(
        key=AuthorityRuleKey(
            selector=AuthoritySelector(
                kind=NODE_KIND_TABLE,
                property_path=PATH_DESC,
                namespace=namespace,
            ),
            authority=AuthorityTarget(provider_type=provider, source_ref=source_ref),
        ),
        declarations=(AuthorityDeclaration(rule_id),),
    )


def _set(*observations: PropertyObservation) -> PropertyObservationSet:
    return PropertyObservationSet.from_observations(observations)


def test_single_observation() -> None:
    report = analyze_property_conflicts(
        _set(_obs("only", _prov("odcs", "c1"))),
        NormalizedAuthorityPolicySet(rules=(_rule("dbt"),)),
    )
    assert len(report.results) == 1
    result = report.results[0]
    assert result.state == "SINGLE_OBSERVATION"
    assert result.reason == "SINGLE_OBSERVATION"
    assert result.has_effective_value is True
    assert result.effective_value == "only"
    assert result.winning_rule_key is None
    payload = result.to_identity_dict()
    assert "effective_value" in payload
    assert "winning_rule_key" not in payload


def test_agreement_multi_provenance_same_value() -> None:
    report = analyze_property_conflicts(
        _set(_obs("shared", _prov("odcs", "c1"), _prov("dbt", "m"))),
        NormalizedAuthorityPolicySet(),
    )
    result = report.results[0]
    assert result.state == "AGREEMENT"
    assert result.reason == "AGREEMENT"
    assert result.has_effective_value is True
    assert result.effective_value == "shared"
    assert result.winning_rule_key is None


def test_resolved_by_authority() -> None:
    observations = _set(
        _obs("from-odcs", _prov("odcs", "c1")),
        _obs("from-dbt", _prov("dbt", "m")),
    )
    authority = NormalizedAuthorityPolicySet(rules=(_rule("odcs", rule_id="odcs-wins"),))
    report = analyze_property_conflicts(observations, authority)
    result = report.results[0]
    assert result.state == "RESOLVED_BY_AUTHORITY"
    assert result.reason == "RESOLVED_BY_AUTHORITY"
    assert result.has_effective_value is True
    assert result.effective_value == "from-odcs"
    assert result.winning_rule_key is not None
    assert result.winning_rule_key.authority.provider_type == "odcs"
    payload = result.to_identity_dict()
    assert payload["winning_rule_key"]["authority"]["provider_type"] == "odcs"
    assert len(result.value_groups) == 2


def test_no_authority_rule() -> None:
    report = analyze_property_conflicts(
        _set(_obs("a", _prov("odcs", "c1")), _obs("b", _prov("dbt", "m"))),
        NormalizedAuthorityPolicySet(),
    )
    result = report.results[0]
    assert result.state == "UNRESOLVED_CONFLICT"
    assert result.reason == "NO_AUTHORITY_RULE"
    assert result.has_effective_value is False
    assert result.winning_rule_key is None
    payload = result.to_identity_dict()
    assert "effective_value" not in payload
    assert "winning_rule_key" not in payload


def test_authoritative_source_not_observed() -> None:
    report = analyze_property_conflicts(
        _set(_obs("a", _prov("odcs", "c1")), _obs("b", _prov("dbt", "m"))),
        NormalizedAuthorityPolicySet(rules=(_rule("openlineage"),)),
    )
    result = report.results[0]
    assert result.state == "UNRESOLVED_CONFLICT"
    assert result.reason == "AUTHORITATIVE_SOURCE_NOT_OBSERVED"
    assert result.has_effective_value is False
    assert result.winning_rule_key is not None
    payload = result.to_identity_dict()
    assert "effective_value" not in payload
    assert "winning_rule_key" in payload


def test_authoritative_source_conflicted() -> None:
    report = analyze_property_conflicts(
        _set(
            _obs("a", _prov("odcs", "c1")),
            _obs("b", _prov("odcs", "c2")),
            _obs("c", _prov("dbt", "m")),
        ),
        NormalizedAuthorityPolicySet(rules=(_rule("odcs"),)),
    )
    result = report.results[0]
    assert result.state == "UNRESOLVED_CONFLICT"
    assert result.reason == "AUTHORITATIVE_SOURCE_CONFLICTED"
    assert result.has_effective_value is False
    assert result.winning_rule_key is not None
    payload = result.to_identity_dict()
    assert "effective_value" not in payload
    assert "winning_rule_key" in payload


def test_invalid_defensive_bypass_same_selector_different_targets() -> None:
    selector = AuthoritySelector(kind=NODE_KIND_TABLE, property_path=PATH_DESC)
    rule_a = NormalizedAuthorityRule(
        key=AuthorityRuleKey(selector=selector, authority=AuthorityTarget("odcs")),
        declarations=(AuthorityDeclaration("a"),),
    )
    rule_b = NormalizedAuthorityRule(
        key=AuthorityRuleKey(selector=selector, authority=AuthorityTarget("dbt")),
        declarations=(AuthorityDeclaration("b"),),
    )
    forward = NormalizedAuthorityPolicySet(rules=(rule_a, rule_b))
    reverse = NormalizedAuthorityPolicySet(rules=(rule_b, rule_a))
    assert forward.content_identity() == reverse.content_identity()

    observations = _set(_obs("a", _prov("odcs", "c1")), _obs("b", _prov("dbt", "m")))
    report_a = analyze_property_conflicts(observations, forward)
    report_b = analyze_property_conflicts(observations, reverse)
    assert report_a.content_identity() == report_b.content_identity()
    result = report_a.results[0]
    assert result.state == "INVALID_OR_AMBIGUOUS_AUTHORITY"
    assert result.reason == "INVALID_OR_AMBIGUOUS_AUTHORITY"
    assert result.winning_rule_key is None
    assert result.has_effective_value is False
    payload = result.to_identity_dict()
    assert "winning_rule_key" not in payload
    assert "effective_value" not in payload


def test_effective_null_present_vs_absent() -> None:
    null_single = analyze_property_conflicts(
        _set(_obs(None, _prov("odcs", "c1"))),
        NormalizedAuthorityPolicySet(),
    ).results[0]
    assert null_single.has_effective_value is True
    assert null_single.effective_value is None
    assert "effective_value" in null_single.to_identity_dict()

    unresolved = analyze_property_conflicts(
        _set(_obs("a", _prov("odcs", "c1")), _obs("b", _prov("dbt", "m"))),
        NormalizedAuthorityPolicySet(),
    ).results[0]
    assert unresolved.has_effective_value is False
    assert "effective_value" not in unresolved.to_identity_dict()


def test_report_root_results_not_conflicts() -> None:
    report = analyze_property_conflicts(_set(), NormalizedAuthorityPolicySet())
    payload = report.to_identity_dict()
    assert "results" in payload
    assert "conflicts" not in payload
    assert payload["results"] == []


def test_namespaced_selector_outranks_general() -> None:
    observations = _set(
        _obs("from-odcs", _prov("odcs", "c1")),
        _obs("from-dbt", _prov("dbt", "m")),
    )
    authority = NormalizedAuthorityPolicySet(
        rules=(
            _rule("odcs", rule_id="general"),
            _rule("dbt", namespace=NS, rule_id="specific"),
        )
    )
    result = analyze_property_conflicts(observations, authority).results[0]
    assert result.state == "RESOLVED_BY_AUTHORITY"
    assert result.effective_value == "from-dbt"
    assert result.winning_rule_key is not None
    assert result.winning_rule_key.selector.namespace == NS

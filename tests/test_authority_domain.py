"""Unit tests for authority domain models."""

from __future__ import annotations

from governance.authority.schema import load_schema
from governance.domain.authority import (
    AUTHORITY_NODE_KINDS,
    AuthorityDeclaration,
    AuthorityRuleKey,
    AuthoritySelector,
    AuthorityTarget,
    NormalizedAuthorityPolicySet,
    NormalizedAuthorityRule,
)
from governance.domain.graph import (
    NODE_KIND_COLUMN,
    NODE_KIND_TABLE,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import PropertyPath

NS = "acme.commerce"
PATH_DESC = PropertyPath(("description",))


def _selector(
    *,
    kind: str = NODE_KIND_TABLE,
    path: PropertyPath = PATH_DESC,
    namespace: str | None = None,
) -> AuthoritySelector:
    return AuthoritySelector(kind=kind, property_path=path, namespace=namespace)


def _target(provider: str = "odcs", source_ref: str | None = None) -> AuthorityTarget:
    return AuthorityTarget(provider_type=provider, source_ref=source_ref)


def test_authority_kinds_parity_with_schema_enum() -> None:
    schema = load_schema()
    enum = schema["$defs"]["select"]["properties"]["kind"]["enum"]
    assert tuple(enum) == AUTHORITY_NODE_KINDS
    assert "database" not in AUTHORITY_NODE_KINDS
    assert "schema" not in AUTHORITY_NODE_KINDS
    assert "relationship" not in AUTHORITY_NODE_KINDS


def test_matching_ranks_namespace_more_specific() -> None:
    general = _selector()
    namespaced = _selector(namespace=NS)
    assert general.specificity_rank == 1
    assert namespaced.specificity_rank == 2

    identity = GraphNodeIdentity(NS, NODE_KIND_TABLE, "orders")
    other = GraphNodeIdentity("other", NODE_KIND_TABLE, "orders")
    assert general.matches(identity, PATH_DESC)
    assert general.matches(other, PATH_DESC)
    assert namespaced.matches(identity, PATH_DESC)
    assert not namespaced.matches(other, PATH_DESC)
    assert not general.matches(identity, PropertyPath(("name",)))
    assert not general.matches(
        GraphNodeIdentity(NS, NODE_KIND_COLUMN, "id", parent=identity),
        PATH_DESC,
    )


def test_target_matches_provider_only_and_source_ref() -> None:
    provider_only = _target("odcs")
    specific = _target("odcs", "customer-contract")
    matching = ProvenanceRecord("odcs", "customer-contract")
    other_ref = ProvenanceRecord("odcs", "other-contract")
    other_provider = ProvenanceRecord("dbt", "customer-contract")
    assert provider_only.matches(matching)
    assert provider_only.matches(other_ref)
    assert not provider_only.matches(other_provider)
    assert specific.matches(matching)
    assert not specific.matches(other_ref)


def test_declarations_retention_two_ids_same_key() -> None:
    key = AuthorityRuleKey(selector=_selector(), authority=_target("odcs"))
    rule = NormalizedAuthorityRule(
        key=key,
        declarations=(
            AuthorityDeclaration("rule-b", "second"),
            AuthorityDeclaration("rule-a", "first"),
            AuthorityDeclaration("rule-a", "first"),
        ),
    )
    assert len(rule.declarations) == 2
    assert [d.config_id for d in rule.declarations] == ["rule-a", "rule-b"]

    policy = NormalizedAuthorityPolicySet(rules=(rule,))
    assert len(policy.rules) == 1
    assert len(policy.rules[0].declarations) == 2


def test_identity_excludes_id_and_description() -> None:
    key = AuthorityRuleKey(selector=_selector(), authority=_target("odcs"))
    a = NormalizedAuthorityPolicySet(
        rules=(
            NormalizedAuthorityRule(
                key=key,
                declarations=(AuthorityDeclaration("rule-a", "alpha"),),
            ),
        )
    )
    b = NormalizedAuthorityPolicySet(
        rules=(
            NormalizedAuthorityRule(
                key=key,
                declarations=(AuthorityDeclaration("rule-b", "beta renamed"),),
            ),
        )
    )
    assert a.content_identity() == b.content_identity()
    payload = a.to_identity_dict()
    for rule in payload["rules"]:
        assert set(rule.keys()) == {"authority", "select"}
        assert "id" not in rule
        assert "description" not in rule
        assert "config_id" not in rule
        assert "declarations" not in rule


def test_ambiguous_programmatic_set_allowed_in_domain() -> None:
    selector = _selector()
    rule_a = NormalizedAuthorityRule(
        key=AuthorityRuleKey(selector=selector, authority=_target("odcs")),
        declarations=(AuthorityDeclaration("a"),),
    )
    rule_b = NormalizedAuthorityRule(
        key=AuthorityRuleKey(selector=selector, authority=_target("dbt")),
        declarations=(AuthorityDeclaration("b"),),
    )
    forward = NormalizedAuthorityPolicySet(rules=(rule_a, rule_b))
    reverse = NormalizedAuthorityPolicySet(rules=(rule_b, rule_a))
    assert len(forward.rules) == 2
    assert forward.content_identity() == reverse.content_identity()
    matching = forward.matching_rules(
        GraphNodeIdentity(NS, NODE_KIND_TABLE, "orders"),
        PATH_DESC,
    )
    assert len(matching) == 2

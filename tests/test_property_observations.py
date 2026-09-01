"""Unit tests for PropertyObservation / Set / Builder."""

from __future__ import annotations

import pytest

from governance.domain.graph import (
    NODE_KIND_TABLE,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationBuilder,
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
    path: PropertyPath = PATH_DESC,
) -> PropertyObservation:
    return PropertyObservation(
        object_identity=identity or _identity(),
        property_path=path,
        value=value,
        provenance=provenances,
    )


def test_value_groups_same_value_unions_provenance() -> None:
    a = _obs("shared", _prov("odcs", "c1"), _prov("dbt", "model.orders"))
    b = _obs("shared", _prov("odcs", "c1"), _prov("openlineage", "evt"))
    grouped = PropertyObservationSet.from_observations([a, b])
    assert len(grouped.observations) == 1
    providers = {p.provider_type for p in grouped.observations[0].provenance}
    assert providers == {"odcs", "dbt", "openlineage"}


def test_value_groups_different_values_remain_separate() -> None:
    a = _obs("alpha", _prov("odcs", "c1"))
    b = _obs("beta", _prov("dbt", "model.orders"))
    grouped = PropertyObservationSet.from_observations([a, b])
    assert len(grouped.observations) == 2
    values = {item.value for item in grouped.observations}
    assert values == {"alpha", "beta"}


def test_value_groups_dedupe_identical_provenance() -> None:
    record = _prov("odcs", "c1")
    a = _obs("v", record, record)
    assert a.provenance == (record,)
    grouped = PropertyObservationSet.from_observations([a, _obs("v", record)])
    assert len(grouped.observations) == 1
    assert grouped.observations[0].provenance == (record,)


def test_non_empty_provenance_required() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        PropertyObservation(
            object_identity=_identity(),
            property_path=PATH_DESC,
            value="x",
            provenance=(),
        )


def test_merge_empty_one_and_n_commutative() -> None:
    empty = PropertyObservationSet.merge()
    assert empty.observations == ()
    assert empty.content_identity() == PropertyObservationSet().content_identity()

    one = PropertyObservationSet.from_observations([_obs("only", _prov("odcs", "c1"))])
    assert PropertyObservationSet.merge(one).content_identity() == one.content_identity()

    a = PropertyObservationSet.from_observations([_obs("a", _prov("odcs", "c1"))])
    b = PropertyObservationSet.from_observations([_obs("b", _prov("dbt", "m"))])
    ab = PropertyObservationSet.merge(a, b)
    ba = PropertyObservationSet.merge(b, a)
    assert ab.content_identity() == ba.content_identity()
    assert {item.value for item in ab.observations} == {"a", "b"}


def test_builder_matches_from_observations_normalizer() -> None:
    identity = _identity()
    p1 = _prov("odcs", "c1")
    p2 = _prov("dbt", "m")
    builder = PropertyObservationBuilder()
    builder.observe(identity, PATH_DESC, "same", p1)
    builder.observe(identity, PATH_DESC, "same", p2)
    built = builder.build()
    direct = PropertyObservationSet.from_observations(
        [_obs("same", p1, identity=identity), _obs("same", p2, identity=identity)]
    )
    assert built.content_identity() == direct.content_identity()
    assert len(built.observations) == 1
    assert len(built.observations[0].provenance) == 2


def test_null_vs_absence() -> None:
    null_obs = PropertyObservationSet.from_observations([_obs(None, _prov("odcs", "c1"))])
    absent = PropertyObservationSet()
    assert null_obs.content_identity() != absent.content_identity()
    assert null_obs.observations[0].value is None
    assert "value" in null_obs.observations[0].to_identity_entry()


def test_derived_mode_material_in_identity() -> None:
    observed = PropertyObservationSet.from_observations(
        [_obs("x", _prov("odcs", "c1", mode="observed"))]
    )
    derived = PropertyObservationSet.from_observations(
        [_obs("x", _prov("odcs", "c1", mode="derived"))]
    )
    assert observed.content_identity() != derived.content_identity()


def test_source_version_material_in_identity() -> None:
    v1 = PropertyObservationSet.from_observations([_obs("x", _prov("odcs", "c1", version="1.0"))])
    v2 = PropertyObservationSet.from_observations([_obs("x", _prov("odcs", "c1", version="2.0"))])
    assert v1.content_identity() != v2.content_identity()


def test_reorder_observations_and_provenance_same_identity() -> None:
    p1 = _prov("odcs", "c1")
    p2 = _prov("dbt", "m")
    forward = PropertyObservationSet.from_observations(
        [_obs("a", p1), _obs("b", p2), _obs("a", p2)]
    )
    reverse = PropertyObservationSet.from_observations([_obs("b", p2), _obs("a", p2, p1)])
    assert forward.content_identity() == reverse.content_identity()


def test_empty_set_valid() -> None:
    empty = PropertyObservationSet()
    payload = empty.to_identity_dict()
    assert payload["observations"] == []
    assert empty.content_identity().algorithm == "sha256"


def test_secrets_not_in_identity() -> None:
    obs = PropertyObservationSet.from_observations(
        [_obs("public-description", _prov("odcs", "customer-contract"))]
    )
    blob = str(obs.to_identity_dict())
    for forbidden in (
        "password",
        "token",
        "bearer",
        "client_secret",
        "DATABASE_URL",
        "COLLIBRA_PASSWORD",
    ):
        assert forbidden not in blob


def test_direct_constructor_normalizes_same_value_and_agrees() -> None:
    from governance.domain.authority import NormalizedAuthorityPolicySet
    from governance.domain.conflicts import analyze_property_conflicts

    odcs = _obs("shared", _prov("odcs", "c1"))
    dbt = _obs("shared", _prov("dbt", "m"))
    direct = PropertyObservationSet(observations=(odcs, dbt))
    via_from = PropertyObservationSet.from_observations([odcs, dbt])
    assert len(direct.observations) == 1
    assert len(direct.observations[0].provenance) == 2
    assert direct.content_identity() == via_from.content_identity()

    report = analyze_property_conflicts(direct, NormalizedAuthorityPolicySet())
    assert len(report.results) == 1
    assert report.results[0].state == "AGREEMENT"
    assert report.results[0].reason == "AGREEMENT"


def test_direct_constructor_dedupes_duplicate_provenance() -> None:
    record = _prov("odcs", "c1")
    a = _obs("v", record)
    b = _obs("v", record)
    direct = PropertyObservationSet(observations=(a, b))
    assert len(direct.observations) == 1
    assert direct.observations[0].provenance == (record,)


def test_direct_constructor_keeps_distinct_values() -> None:
    direct = PropertyObservationSet(
        observations=(_obs("A", _prov("odcs", "c1")), _obs("B", _prov("dbt", "m")))
    )
    assert {item.value for item in direct.observations} == {"A", "B"}


def test_direct_constructor_reorder_same_identity() -> None:
    a = _obs("A", _prov("odcs", "c1"))
    b = _obs("B", _prov("dbt", "m"))
    forward = PropertyObservationSet(observations=(a, b))
    reverse = PropertyObservationSet(observations=(b, a))
    assert forward.content_identity() == reverse.content_identity()

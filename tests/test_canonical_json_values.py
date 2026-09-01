"""Unit tests for shared canonical JSON value helper."""

from __future__ import annotations

import math

import pytest

from governance.domain.graph import (
    NODE_KIND_TABLE,
    GovernanceGraph,
    GraphNode,
    GraphNodeIdentity,
)
from governance.identity.json_values import (
    CanonicalArray,
    CanonicalObject,
    canonicalize_json_value,
    normalize_json_value,
)


def test_accepts_supported_json_types() -> None:
    assert canonicalize_json_value(None) is None
    assert canonicalize_json_value(True) is True
    assert canonicalize_json_value(False) is False
    assert canonicalize_json_value(42) == 42
    assert canonicalize_json_value(1.5) == 1.5
    assert canonicalize_json_value("text") == "text"
    obj = canonicalize_json_value({"b": 1, "a": 2})
    assert isinstance(obj, CanonicalObject)
    assert obj.items == (("a", 2), ("b", 1))
    arr = canonicalize_json_value([1, {"z": True}])
    assert isinstance(arr, CanonicalArray)
    assert arr.items[0] == 1


def test_rejects_nan_inf_non_string_keys_sets_bytes() -> None:
    with pytest.raises(ValueError, match="finite"):
        canonicalize_json_value(math.nan)
    with pytest.raises(ValueError, match="finite"):
        canonicalize_json_value(math.inf)
    with pytest.raises(ValueError, match="finite"):
        canonicalize_json_value(-math.inf)
    with pytest.raises(TypeError, match="keys must be strings"):
        canonicalize_json_value({1: "x"})
    with pytest.raises(TypeError, match="unsupported"):
        canonicalize_json_value({1, 2})
    with pytest.raises(TypeError, match="unsupported"):
        canonicalize_json_value(frozenset({1}))
    with pytest.raises(TypeError, match="unsupported"):
        canonicalize_json_value(b"abc")
    with pytest.raises(TypeError, match="unsupported"):
        canonicalize_json_value(bytearray(b"abc"))
    with pytest.raises(TypeError, match="unsupported"):
        canonicalize_json_value(object())


def test_int_not_equal_float_one() -> None:
    from governance.identity.json_values import canonical_value_fingerprint

    assert canonical_value_fingerprint(1) != canonical_value_fingerprint(1.0)
    assert normalize_json_value(1) == 1
    assert normalize_json_value(1.0) == 1.0
    assert type(normalize_json_value(1)) is int
    assert type(normalize_json_value(1.0)) is float


def test_bool_true_not_equal_int_one() -> None:
    from governance.identity.json_values import canonical_value_fingerprint

    assert canonical_value_fingerprint(True) != canonical_value_fingerprint(1)
    assert normalize_json_value(True) is True
    assert normalize_json_value(1) == 1


def test_negative_zero_preserved_via_json_dumps_contract() -> None:
    # json.dumps treats -0.0 distinctly from 0.0 in Python's default float repr path
    # used by canonical_json_bytes; fingerprints must remain distinct.
    from governance.identity.json_values import canonical_value_fingerprint

    assert canonical_value_fingerprint(0.0) != canonical_value_fingerprint(-0.0)


def test_map_keys_sorted_sequence_order_material() -> None:
    a = canonicalize_json_value({"b": 1, "a": 2})
    b = canonicalize_json_value({"a": 2, "b": 1})
    assert a == b
    left = canonicalize_json_value([1, 2])
    right = canonicalize_json_value([2, 1])
    assert left != right


def test_tuple_equals_list_in_plain_json() -> None:
    assert normalize_json_value((1, 2)) == normalize_json_value([1, 2])
    assert canonicalize_json_value((1, {"a": 1})) == canonicalize_json_value([1, {"a": 1}])


def test_graph_regression_uses_shared_helper() -> None:
    identity = GraphNodeIdentity("acme", NODE_KIND_TABLE, "orders")
    as_bool = GraphNode(identity=identity, name="orders", attributes={"flag": True})
    as_int = GraphNode(identity=identity, name="orders", attributes={"flag": 1})
    assert as_bool.attributes_canonical != as_int.attributes_canonical

    g_bool = GovernanceGraph.from_parts([as_bool])
    g_int = GovernanceGraph.from_parts(
        [GraphNode(identity=identity, name="orders", attributes={"flag": 1})]
    )
    assert g_bool.content_identity() != g_int.content_identity()

    with pytest.raises(ValueError, match="finite"):
        GraphNode(identity=identity, name="orders", attributes={"x": math.nan})

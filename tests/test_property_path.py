"""Unit tests for RFC6901 PropertyPath parse/serialize."""

from __future__ import annotations

import pytest

from governance.domain.observations import PropertyObservationBuilder, PropertyPath


def test_parse_to_pointer_round_trip_simple_and_nested() -> None:
    simple = PropertyPath.parse("/description")
    assert simple.segments == ("description",)
    assert simple.to_pointer() == "/description"

    nested = PropertyPath.parse("/attributes/storage_layer")
    assert nested.segments == ("attributes", "storage_layer")
    assert nested.to_pointer() == "/attributes/storage_layer"


def test_rfc6901_escapes_tilde_and_slash() -> None:
    path = PropertyPath.parse("/a~1b/c~0d")
    assert path.segments == ("a/b", "c~d")
    assert path.to_pointer() == "/a~1b/c~0d"

    constructed = PropertyPath(("a/b", "c~d"))
    assert constructed.to_pointer() == "/a~1b/c~0d"
    assert PropertyPath.parse(constructed.to_pointer()) == constructed


def test_empty_segment_and_attributes_trailing_slash() -> None:
    with_empty = PropertyPath.parse("/attributes/")
    assert with_empty.segments == ("attributes", "")
    assert with_empty.to_pointer() == "/attributes/"

    without = PropertyPath.parse("/attributes")
    assert without.segments == ("attributes",)
    assert with_empty != without
    assert with_empty.to_pointer() != without.to_pointer()


def test_root_empty_pointer_rejected() -> None:
    with pytest.raises(ValueError, match="root pointer"):
        PropertyPath.parse("")


def test_non_string_parse_type_error() -> None:
    with pytest.raises(TypeError, match="string"):
        PropertyPath.parse(["/description"])  # type: ignore[arg-type]


def test_invalid_escape_rejected() -> None:
    with pytest.raises(ValueError, match="invalid JSON Pointer escape"):
        PropertyPath.parse("/a~2b")
    with pytest.raises(ValueError, match="invalid JSON Pointer escape"):
        PropertyPath.parse("/trailing~")


def test_must_start_with_slash() -> None:
    with pytest.raises(ValueError, match="must start with '/'"):
        PropertyPath.parse("description")
    with pytest.raises(ValueError, match="must start with '/'"):
        PropertyPath.parse("#/description")


def test_no_full_pointer_strip_spaces_are_material() -> None:
    path = PropertyPath.parse("/ description")
    assert path.segments == (" description",)
    assert path.to_pointer() == "/ description"


def test_constructor_requires_non_empty_string_segments() -> None:
    with pytest.raises(ValueError, match="at least one segment"):
        PropertyPath(())
    with pytest.raises(TypeError, match="strings"):
        PropertyPath((1,))  # type: ignore[arg-type]


def test_builder_requires_property_path_not_raw_string() -> None:
    builder = PropertyObservationBuilder()
    from governance.domain.graph import NODE_KIND_TABLE, GraphNodeIdentity, ProvenanceRecord

    identity = GraphNodeIdentity("ns", NODE_KIND_TABLE, "orders")
    provenance = ProvenanceRecord("odcs", "contract-1")
    with pytest.raises(TypeError, match="PropertyPath"):
        builder.observe(identity, "/description", "x", provenance)  # type: ignore[arg-type]

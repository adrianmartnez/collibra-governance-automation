"""Core compare semantics tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest_comparison import build_snapshot
from governance.comparison import (
    ComparisonError,
    RootAlignmentAck,
    assert_inverse,
    build_comparison_result,
)
from governance.comparison.errors import CODE_ROOT_ALIGNMENT_REQUIRED
from governance.domain import Column, make_column_id
from governance.snapshots import write_snapshot


def test_identical_snapshots() -> None:
    a = build_snapshot()
    b = build_snapshot()
    result = build_comparison_result(a, b)
    assert result["status"] == "identical"
    assert result["summary"]["added"] == 0
    assert result["summary"]["removed"] == 0
    assert result["summary"]["changed"] == 0
    assert result["summary"]["property_changes"] == 0
    assert result["object_changes"] == []
    assert result["writes_performed"] == 0
    assert result["summary"]["unchanged"] > 0


def test_added_and_removed_column() -> None:
    baseline = build_snapshot()
    extra = Column(
        id=make_column_id("governance-demo", "governance_demo", "sales", "orders", "extra"),
        name="extra",
        data_type="text",
        ordinal_position=2,
        nullable=True,
    )
    candidate = build_snapshot(extra_column=extra)
    result = build_comparison_result(baseline, candidate)
    assert result["status"] == "different"
    assert result["summary"]["added"] == 1
    assert result["summary"]["removed"] == 0
    added = [c for c in result["object_changes"] if c["change"] == "added"]
    assert len(added) == 1
    assert added[0]["object_identity"]["kind"] == "column"
    assert added[0]["property_changes"] == []

    reverse = build_comparison_result(candidate, baseline)
    assert reverse["summary"]["removed"] == 1
    assert reverse["summary"]["added"] == 0


def test_changed_description() -> None:
    baseline = build_snapshot(description=None)
    candidate = build_snapshot(description="updated")
    result = build_comparison_result(baseline, candidate)
    assert result["status"] == "different"
    assert result["summary"]["changed"] >= 1
    changed = [c for c in result["object_changes"] if c["change"] == "changed"]
    assert any(
        any(p["property"] == "/description" for p in item["property_changes"]) for item in changed
    )


def test_technical_attr_missing_vs_null() -> None:
    baseline = build_snapshot(technical_attributes={})
    candidate = build_snapshot(technical_attributes={"flag": None})
    result = build_comparison_result(baseline, candidate)
    assert result["status"] == "different"
    props = []
    for item in result["object_changes"]:
        if item["change"] == "changed":
            props.extend(item["property_changes"])
    tech = [p for p in props if p["property"].startswith("/technical_attributes/")]
    assert tech
    assert any(
        p["baseline"] == {"has_value": False}
        and p["candidate"] == {"has_value": True, "value": None}
        for p in tech
    )


def test_rename_table_is_removed_and_added() -> None:
    baseline = build_snapshot(table_name="orders")
    candidate = build_snapshot(table_name="orders_v2")
    result = build_comparison_result(baseline, candidate)
    assert result["summary"]["changed"] == 0 or all(
        not any(p["property"] == "/name" for p in c["property_changes"])
        for c in result["object_changes"]
        if c["change"] == "changed"
    )
    assert result["summary"]["added"] >= 1
    assert result["summary"]["removed"] >= 1
    kinds = {
        (c["change"], c["object_identity"]["kind"])
        for c in result["object_changes"]
        if c["object_identity"]["kind"] == "table"
    }
    assert ("added", "table") in kinds
    assert ("removed", "table") in kinds


def test_root_alignment_required() -> None:
    baseline = build_snapshot(source_name="dev")
    candidate = build_snapshot(source_name="prod")
    with pytest.raises(ComparisonError) as exc_info:
        build_comparison_result(baseline, candidate)
    assert any(e.code == CODE_ROOT_ALIGNMENT_REQUIRED for e in exc_info.value.errors)


def test_root_alignment_matches_but_name_changed() -> None:
    baseline = build_snapshot(source_name="dev", database_name="dev_db")
    candidate = build_snapshot(source_name="prod", database_name="prod_db")
    result = build_comparison_result(
        baseline,
        candidate,
        ack=RootAlignmentAck(align_source_roots=True, align_database_roots=True),
    )
    assert result["status"] == "different"
    assert result["root_alignment"]["source"] == {"baseline": "dev", "candidate": "prod"}
    assert result["root_alignment"]["database"] == {
        "baseline": "dev_db",
        "candidate": "prod_db",
    }
    # No mass add/remove of interior objects
    assert result["summary"]["added"] == 0
    assert result["summary"]["removed"] == 0
    assert result["summary"]["changed"] >= 1
    name_changes = [
        p
        for c in result["object_changes"]
        if c["change"] == "changed"
        for p in c["property_changes"]
        if p["property"] == "/name"
    ]
    assert name_changes


def test_redundant_alignment_noop_identity() -> None:
    a = build_snapshot()
    b = build_snapshot()
    without = build_comparison_result(a, b)
    with_flags = build_comparison_result(
        a,
        b,
        ack=RootAlignmentAck(align_source_roots=True, align_database_roots=True),
    )
    assert without["content_identity"] == with_flags["content_identity"]
    assert with_flags["root_alignment"]["source"] is None
    assert with_flags["root_alignment"]["database"] is None


def test_cross_root_refs_unchanged_when_aligned() -> None:
    baseline = build_snapshot(source_name="dev", database_name="dev_db")
    candidate = build_snapshot(source_name="prod", database_name="prod_db")
    result = build_comparison_result(
        baseline,
        candidate,
        ack=RootAlignmentAck(align_source_roots=True, align_database_roots=True),
    )
    # FK/relationship should not appear as changed solely due to raw id roots
    changed_kinds = {
        c["object_identity"]["kind"] for c in result["object_changes"] if c["change"] == "changed"
    }
    assert "foreign_key" not in changed_kinds
    assert "relationship" not in changed_kinds
    assert "primary_key" not in changed_kinds


def test_reversed_comparison() -> None:
    baseline = build_snapshot(description=None)
    candidate = build_snapshot(description="x")
    left = build_comparison_result(baseline, candidate)
    right = build_comparison_result(candidate, baseline)
    assert_inverse(left, right)
    assert left["content_identity"] != right["content_identity"]


def test_reordered_json_keys_unchanged(tmp_path: Path) -> None:
    snapshot = build_snapshot()
    path = tmp_path / "snap.json"
    write_snapshot(snapshot, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Re-dump with different key order but same content_identity
    reordered = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    path2 = tmp_path / "reordered.json"
    path2.write_text(reordered, encoding="utf-8")
    from governance.snapshots import load_snapshot

    loaded = load_snapshot(path2)
    result = build_comparison_result(snapshot, loaded)
    assert result["status"] == "identical"

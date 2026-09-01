"""Pytest helpers for drift tests."""

from __future__ import annotations

import json
from pathlib import Path

from conftest_comparison import build_snapshot
from governance.comparison import (
    RootAlignmentAck,
    build_comparison_result,
    write_comparison_artifact,
)
from governance.identity.hashing import snapshot_comparison_identity


def write_comparison_artifact_for_snapshots(
    baseline,
    candidate,
    path: str | Path,
    *,
    ack=None,
) -> dict:
    alignment = ack if ack is not None else RootAlignmentAck()
    result = build_comparison_result(baseline, candidate, ack=alignment)
    write_comparison_artifact(result, path)
    return result


def write_identical_comparison(path: str | Path) -> dict:
    snap = build_snapshot()
    return write_comparison_artifact_for_snapshots(snap, snap, path)


def write_different_description_comparison(path: str | Path) -> dict:
    return write_comparison_artifact_for_snapshots(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
        path,
    )


def write_aligned_root_comparison(path: str | Path) -> dict:
    baseline = build_snapshot(source_name="dev", database_name="dev_db")
    candidate = build_snapshot(source_name="prod", database_name="prod_db")
    return write_comparison_artifact_for_snapshots(
        baseline,
        candidate,
        path,
        ack=RootAlignmentAck(align_source_roots=True, align_database_roots=True),
    )


def write_rehashed_comparison(path: str | Path, payload: dict) -> dict:
    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = snapshot_comparison_identity(without_identity).to_dict()
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    Path(path).write_text(text, encoding="utf-8")
    return payload


def sort_object_changes(payload: dict) -> None:
    from governance.comparison.compare import CHANGE_RANK
    from governance.comparison.projection import ComparisonObjectIdentity

    payload["object_changes"].sort(
        key=lambda item: (
            CHANGE_RANK[item["change"]],
            ComparisonObjectIdentity(
                kind=item["object_identity"]["kind"],
                path=tuple(item["object_identity"]["path"]),
            ).canonical_bytes(),
        )
    )


def inject_forged_property_change(
    path: str | Path,
    *,
    kind: str,
    identity_path: list[str],
    parent_identity: dict | None,
    property_pointer: str,
    baseline: dict,
    candidate: dict,
) -> dict:
    write_different_description_comparison(path)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["object_changes"].append(
        {
            "change": "changed",
            "object_identity": {"kind": kind, "path": identity_path},
            "parent_identity": parent_identity,
            "property_changes": [
                {
                    "property": property_pointer,
                    "baseline": baseline,
                    "candidate": candidate,
                }
            ],
        }
    )
    sort_object_changes(payload)
    payload["summary"]["changed"] = sum(
        1 for item in payload["object_changes"] if item["change"] == "changed"
    )
    payload["summary"]["property_changes"] = sum(
        len(item["property_changes"])
        for item in payload["object_changes"]
        if item["change"] == "changed"
    )
    payload["status"] = "different"
    return write_rehashed_comparison(path, payload)


def write_policy(path: str | Path, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def write_full_description_policy(path: str | Path) -> None:
    write_policy(
        path,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-source-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
  - id: allow-table-description
    match:
      change: changed
      object:
        kind: table
        path: ["sales", "orders"]
      property: /description
""",
    )


def write_technical_attribute_comparison(
    path: str | Path, *, baseline_attrs, candidate_attrs
) -> dict:
    from governance.comparison import write_comparison_artifact

    baseline = build_snapshot(technical_attributes=dict(baseline_attrs or {}))
    candidate = build_snapshot(technical_attributes=dict(candidate_attrs or {}))
    result = build_comparison_result(baseline, candidate)
    write_comparison_artifact(result, path)
    return result

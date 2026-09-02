"""Serialize, persist, and load governance snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from governance.io.atomic import atomic_write_text
from governance.snapshots.errors import SnapshotIOError
from governance.snapshots.load import load_snapshot_artifact
from governance.snapshots.models import GovernanceSnapshot


def snapshot_to_json(snapshot: GovernanceSnapshot) -> str:
    return (
        json.dumps(
            snapshot.to_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )


def write_snapshot(snapshot: GovernanceSnapshot, output_path: str | Path) -> Path:
    target = Path(output_path)
    try:
        return atomic_write_text(target, snapshot_to_json(snapshot))
    except OSError as exc:
        raise SnapshotIOError(
            f"Unable to write snapshot to {target}",
            code="write_error",
            path="/output",
        ) from exc


def load_snapshot(path: str | Path) -> GovernanceSnapshot:
    """Load a persisted snapshot artifact (delegates to strict loader)."""
    return load_snapshot_artifact(path)

"""Atomic UTF-8 text file writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(target: Path, payload: str) -> Path:
    """Write ``payload`` to ``target`` atomically (temp file + replace)."""
    if target.exists() and target.is_dir():
        raise OSError(f"Output path is a directory: {target}")

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        return target
    except Exception:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

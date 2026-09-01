"""Snapshot artifact errors."""

from __future__ import annotations


class SnapshotError(RuntimeError):
    """Base snapshot error with optional stable diagnostic code/path."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_snapshot_payload",
        path: str = "/",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


class SnapshotCompatibilityError(SnapshotError):
    """Unsupported or incompatible snapshot contract."""


class SnapshotIntegrityError(SnapshotError):
    """Persisted content_identity does not match recomputed digest."""


class SnapshotIOError(SnapshotError):
    """Snapshot filesystem I/O failure."""

"""Snapshot artifact errors."""

from __future__ import annotations


class SnapshotError(RuntimeError):
    """Base snapshot error."""


class SnapshotCompatibilityError(SnapshotError):
    """Unsupported or incompatible snapshot contract."""


class SnapshotIntegrityError(SnapshotError):
    """Persisted content_identity does not match recomputed digest."""


class SnapshotIOError(SnapshotError):
    """Snapshot filesystem I/O failure."""

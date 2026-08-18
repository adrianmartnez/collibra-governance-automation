"""Conservative, deterministic Import/sync command batching.

Counters are a project safety ceiling, not a claim of Collibra's internal
resource accounting. Configured limits may only lower the hard maxima.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from governance.config import (
    DEFAULT_COLLIBRA_BATCH_MAX_ADDITIONAL_CHARACTERISTICS,
    DEFAULT_COLLIBRA_BATCH_MAX_RESOURCES,
    require_strict_positive_int,
)
from governance.identity.hashing import ALGORITHM, HASHING_CONTRACT_VERSION, ContentIdentity
from governance.integrations.collibra.adapters import CollibraAdapterError

HARD_MAX_RESOURCES = DEFAULT_COLLIBRA_BATCH_MAX_RESOURCES
HARD_MAX_ADDITIONAL_CHARACTERISTICS = DEFAULT_COLLIBRA_BATCH_MAX_ADDITIONAL_CHARACTERISTICS
_IMPORT_BATCH_IDENTITY_PREFIX = b"gov-import-batch-v1\n"

if TYPE_CHECKING:
    from governance.integrations.collibra.import_api import ImportDocument


class BatchingError(CollibraAdapterError):
    """A command or configured limit cannot be batched safely."""

    def __init__(self, message: str) -> None:
        super().__init__(message, operation="partition_import")


@dataclass(frozen=True, slots=True)
class CommandCounts:
    resource_count: int
    attribute_characteristic_count: int
    relation_characteristic_count: int
    tag_characteristic_count: int = 0

    @property
    def additional_characteristic_count(self) -> int:
        return (
            self.attribute_characteristic_count
            + self.relation_characteristic_count
            + self.tag_characteristic_count
        )

    def plus(self, other: CommandCounts) -> CommandCounts:
        return CommandCounts(
            resource_count=self.resource_count + other.resource_count,
            attribute_characteristic_count=(
                self.attribute_characteristic_count + other.attribute_characteristic_count
            ),
            relation_characteristic_count=(
                self.relation_characteristic_count + other.relation_characteristic_count
            ),
            tag_characteristic_count=self.tag_characteristic_count + other.tag_characteristic_count,
        )


ZERO_COUNTS = CommandCounts(0, 0, 0, 0)


def import_batch_content_identity(document: ImportDocument) -> ContentIdentity:
    """Deterministic SHA-256 identity for one partitioned Import batch."""
    digest = hashlib.sha256(_IMPORT_BATCH_IDENTITY_PREFIX + document.canonical_json()).hexdigest()
    return ContentIdentity(
        algorithm=ALGORITHM,
        hashing_contract_version=HASHING_CONTRACT_VERSION,
        digest=digest,
    )


def batch_document_counts(document: ImportDocument) -> CommandCounts:
    total = ZERO_COUNTS
    for command in document.commands:
        total = total.plus(count_command_payload(command.to_dict()))
    return total


def resolve_batch_limits(
    max_resources: int | None = None,
    max_additional_characteristics: int | None = None,
) -> tuple[int, int]:
    resources = (
        HARD_MAX_RESOURCES
        if max_resources is None
        else require_strict_positive_int(max_resources, "max_resources")
    )
    additional = (
        HARD_MAX_ADDITIONAL_CHARACTERISTICS
        if max_additional_characteristics is None
        else require_strict_positive_int(
            max_additional_characteristics,
            "max_additional_characteristics",
        )
    )
    if resources <= 0 or additional <= 0:
        raise BatchingError("batch ceilings must be positive")
    if resources > HARD_MAX_RESOURCES or additional > HARD_MAX_ADDITIONAL_CHARACTERISTICS:
        raise BatchingError("batch ceilings cannot exceed the hard maxima")
    return resources, additional


def count_command_payload(payload: dict[str, Any]) -> CommandCounts:
    """Count one Import command using the conservative project counters."""
    resource_count = 1
    attribute_count = _entry_count(payload.get("attributes"))
    relation_count = _entry_count(payload.get("relations"))
    return CommandCounts(
        resource_count=resource_count,
        attribute_characteristic_count=attribute_count,
        relation_characteristic_count=relation_count,
        tag_characteristic_count=0,
    )


def _entry_count(container: Any) -> int:
    if not isinstance(container, dict):
        return 0
    total = 0
    for values in container.values():
        if isinstance(values, list):
            total += len(values)
        elif values is not None:
            total += 1
    return total


def partition_counts(
    counts: tuple[CommandCounts, ...] | list[CommandCounts],
    *,
    max_resources: int,
    max_additional_characteristics: int,
) -> tuple[tuple[int, ...], ...]:
    """Greedy stable grouping of command indices within conservative ceilings."""
    max_resources, max_additional_characteristics = resolve_batch_limits(
        max_resources, max_additional_characteristics
    )
    groups: list[tuple[int, ...]] = []
    current_indexes: list[int] = []
    current = ZERO_COUNTS
    for index, item in enumerate(counts):
        if (
            item.resource_count > max_resources
            or item.additional_characteristic_count > max_additional_characteristics
        ):
            raise BatchingError("a single import command exceeds the batch ceiling")
        combined = current.plus(item)
        if current_indexes and (
            combined.resource_count > max_resources
            or combined.additional_characteristic_count > max_additional_characteristics
        ):
            groups.append(tuple(current_indexes))
            current_indexes = [index]
            current = item
            continue
        current_indexes.append(index)
        current = combined
    if current_indexes:
        groups.append(tuple(current_indexes))
    return tuple(groups)


def partition_document(
    document: ImportDocument,
    *,
    max_resources: int | None = None,
    max_additional_characteristics: int | None = None,
) -> tuple[ImportDocument, ...]:
    from governance.integrations.collibra.import_api import ImportDocument as Document

    max_resources, max_additional_characteristics = resolve_batch_limits(
        max_resources, max_additional_characteristics
    )
    if not document.commands:
        return ()
    counts = tuple(count_command_payload(command.to_dict()) for command in document.commands)
    groups = partition_counts(
        counts,
        max_resources=max_resources,
        max_additional_characteristics=max_additional_characteristics,
    )
    return tuple(
        Document(commands=tuple(document.commands[index] for index in group)) for group in groups
    )

"""YAML loader that rejects duplicate mapping keys and non-string keys."""

from __future__ import annotations

from typing import Any

import yaml


class DuplicateKeyError(ValueError):
    """Raised when a YAML mapping contains duplicate keys."""


class InvalidMappingKeyError(ValueError):
    """Raised when a YAML mapping key is not a bounded string."""


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys at any depth."""


def _construct_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise InvalidMappingKeyError("drift policy mapping keys must be strings")
        if key in mapping:
            raise DuplicateKeyError("duplicate mapping key in drift policy YAML")
        value = loader.construct_object(value_node, deep=deep)
        mapping[key] = value
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_drift_policy_yaml(text: str) -> Any:
    documents = list(yaml.load_all(text, Loader=UniqueKeyLoader))
    if len(documents) != 1:
        raise ValueError("drift policy YAML must contain exactly one document")
    return documents[0]

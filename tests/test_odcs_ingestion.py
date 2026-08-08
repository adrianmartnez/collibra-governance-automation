"""Tests for ODCS v3.1.0 document validation and GovernanceGraph mapping."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema.validators import Draft202012Validator, validator_for

from governance import __version__
from governance.integrations.odcs import (
    OdcsParseError,
    OdcsReadError,
    OdcsSchemaError,
    OdcsUnsupportedVersionError,
    load_odcs_document,
    validate_odcs_document,
)
from governance.integrations.odcs.schema import (
    ODCS_SCHEMA_SHA256,
    load_odcs_schema,
)

NS = "acme.commerce"


def _minimal_contract(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
    }
    doc.update(overrides)
    return doc


def _write(path: Path, document: dict[str, Any], *, fmt: str = "yaml") -> Path:
    if fmt == "yaml":
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- A: Parse/read ---


def test_load_valid_yaml_and_json(tmp_path: Path) -> None:
    doc = _minimal_contract()
    yaml_path = _write(tmp_path / "c.odcs.yaml", doc, fmt="yaml")
    json_path = _write(tmp_path / "c.json", doc, fmt="json")
    assert load_odcs_document(yaml_path)["id"] == "contract-orders"
    assert load_odcs_document(json_path)["id"] == "contract-orders"


def test_load_odcs_yml_suffix(tmp_path: Path) -> None:
    path = _write(tmp_path / "c.odcs.yml", _minimal_contract(), fmt="yaml")
    assert load_odcs_document(path)["apiVersion"] == "v3.1.0"


def test_unsupported_suffix(tmp_path: Path) -> None:
    path = tmp_path / "c.txt"
    path.write_text("apiVersion: v3.1.0\n", encoding="utf-8")
    with pytest.raises(OdcsReadError) as exc:
        load_odcs_document(path)
    assert exc.value.errors[0].code == "odcs_read_error"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OdcsReadError):
        load_odcs_document(tmp_path / "missing.yaml")


def test_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(":\n  - invalid\n", encoding="utf-8")
    with pytest.raises(OdcsParseError) as exc:
        load_odcs_document(path)
    assert exc.value.errors[0].code == "odcs_parse_error"


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OdcsParseError):
        load_odcs_document(path)


def test_unsafe_yaml_tag_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text("!!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    with pytest.raises(OdcsParseError):
        load_odcs_document(path)


def test_root_non_object_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(OdcsParseError) as exc:
        load_odcs_document(path)
    assert "mapping" in exc.value.errors[0].message


def test_load_does_not_mutate_and_returns_copy(tmp_path: Path) -> None:
    doc = _minimal_contract(tags=["a"])
    path = _write(tmp_path / "c.yaml", doc)
    loaded = load_odcs_document(path)
    loaded["tags"].append("b")
    loaded2 = load_odcs_document(path)
    assert loaded2["tags"] == ["a"]


# --- B: Version/schema ---


def test_api_version_v310_accepted() -> None:
    validated = validate_odcs_document(_minimal_contract())
    assert validated["apiVersion"] == "v3.1.0"


def test_missing_api_version() -> None:
    doc = _minimal_contract()
    del doc["apiVersion"]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert exc.value.errors[0].path == "/apiVersion"
    assert exc.value.errors[0].code == "odcs_schema_error"


def test_v302_rejected_as_unsupported() -> None:
    with pytest.raises(OdcsUnsupportedVersionError) as exc:
        validate_odcs_document(_minimal_contract(apiVersion="v3.0.2"))
    assert exc.value.errors[0].code == "odcs_unsupported_version"
    assert exc.value.errors[0].path == "/apiVersion"


def test_v22x_rejected_as_unsupported() -> None:
    with pytest.raises(OdcsUnsupportedVersionError) as exc:
        validate_odcs_document(_minimal_contract(apiVersion="v2.2.2"))
    assert exc.value.errors[0].code == "odcs_unsupported_version"


def test_wrong_kind() -> None:
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(_minimal_contract(kind="SomethingElse"))
    assert any(e.path == "/kind" for e in exc.value.errors)


def test_missing_version_and_status_have_distinct_paths() -> None:
    doc = _minimal_contract()
    del doc["version"]
    del doc["status"]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    by_path = {e.path: e for e in exc.value.errors}
    assert "/version" in by_path
    assert "/status" in by_path
    assert by_path["/version"].message == "missing required property"
    assert by_path["/status"].message == "missing required property"


def test_nested_missing_name_has_exact_path() -> None:
    doc = _minimal_contract(schema=[{}])
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert any(
        e.path == "/schema/0/name" and e.message == "missing required property"
        for e in exc.value.errors
    )


def test_unknown_top_level_properties_are_actionable_and_deduped() -> None:
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(_minimal_contract(foo=1, bar=2))
    unknown = [e for e in exc.value.errors if e.message == "unknown property is not allowed"]
    paths = [e.path for e in unknown]
    assert paths.count("/foo") == 1
    assert paths.count("/bar") == 1
    assert paths == sorted(paths)


def test_unknown_nested_property_has_actionable_pointer() -> None:
    doc = _minimal_contract(schema=[{"name": "orders", "notReal": True}])
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert any(
        e.path == "/schema/0/notReal" and e.message == "unknown property is not allowed"
        for e in exc.value.errors
    )


def test_invalid_nested_property_type() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [{"name": "id", "required": "yes"}],
            }
        ]
    )
    with pytest.raises(OdcsSchemaError):
        validate_odcs_document(doc)


def test_diagnostics_ordered_by_path() -> None:
    doc = _minimal_contract(extraOne=1, extraTwo=2)
    del doc["status"]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    keys = [(e.path, e.code, e.message) for e in exc.value.errors]
    assert keys == sorted(keys)
    assert any(e.path == "/status" for e in exc.value.errors)
    assert any(e.path == "/extraOne" for e in exc.value.errors)
    assert any(e.path == "/extraTwo" for e in exc.value.errors)


def test_bundled_schema_loads_offline() -> None:
    schema = load_odcs_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2019-09/schema"
    assert "apiVersion" in schema["properties"]


def test_validator_follows_2019_09_not_hardcoded_2020_12() -> None:
    schema = load_odcs_schema()
    cls = validator_for(schema)
    assert cls is not Draft202012Validator
    assert "201909" in cls.__name__ or "2019" in cls.__name__.lower()


def test_cyclic_mapping_rejected_without_recursion_error() -> None:
    doc: dict[str, Any] = _minimal_contract()
    doc["self"] = doc  # type: ignore[assignment]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert exc.value.errors[0].message == "document is not a finite JSON tree"


def test_cyclic_list_rejected() -> None:
    lst: list[Any] = []
    lst.append(lst)
    doc = _minimal_contract(schema=lst)
    with pytest.raises(OdcsSchemaError):
        validate_odcs_document(doc)


def test_json_nan_rejected_as_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text(
        '{"apiVersion":"v3.1.0","kind":"DataContract","id":"x",'
        '"version":"1","status":"active","score":NaN}',
        encoding="utf-8",
    )
    with pytest.raises(OdcsParseError):
        load_odcs_document(path)


def test_json_infinity_rejected_as_parse_error(tmp_path: Path) -> None:
    for literal in ("Infinity", "-Infinity"):
        path = tmp_path / f"{literal}.json"
        path.write_text(
            '{"apiVersion":"v3.1.0","kind":"DataContract","id":"x",'
            f'"version":"1","status":"active","score":{literal}}}',
            encoding="utf-8",
        )
        with pytest.raises(OdcsParseError):
            load_odcs_document(path)


def test_direct_non_finite_float_rejected_as_schema_error() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        doc = _minimal_contract()
        doc["tenant"] = value  # type: ignore[assignment]
        with pytest.raises(OdcsSchemaError) as exc:
            validate_odcs_document(doc)
        assert exc.value.errors[0].message == "document is not a finite JSON tree"


def test_yaml_non_finite_rejected_at_validation(tmp_path: Path) -> None:
    path = tmp_path / "nan.yaml"
    path.write_text(
        "apiVersion: v3.1.0\nkind: DataContract\nid: x\nversion: '1'\n"
        "status: active\nscore: .nan\n",
        encoding="utf-8",
    )
    loaded = load_odcs_document(path)
    with pytest.raises(OdcsSchemaError):
        validate_odcs_document(loaded)


def test_max_depth_64_rejected() -> None:
    nested: Any = "leaf"
    for _ in range(65):
        nested = {"child": nested}
    doc = _minimal_contract()
    doc["description"] = {"purpose": "x", "usage": nested}
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert exc.value.errors[0].message == "document is not a finite JSON tree"


def test_non_json_compatible_type_rejected() -> None:
    doc = _minimal_contract()
    doc["tenant"] = object()  # type: ignore[assignment]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert exc.value.errors[0].message == "document is not a finite JSON tree"


# --- J: Packaging / integrity ---


def test_schema_and_attribution_packaged() -> None:
    root = files("governance.integrations.odcs.schemas")
    schema_text = root.joinpath("odcs-json-schema-v3.1.0.json").read_text(encoding="utf-8")
    assert "Open Data Contract Standard" in schema_text
    license_text = root.joinpath("LICENSE-Apache-2.0.txt").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    notice = root.joinpath("ODCS-SCHEMA-NOTICE.txt").read_text(encoding="utf-8")
    assert "v3.1.0" in notice
    assert "Apache License 2.0" in notice


def test_pinned_schema_sha256_matches_upstream_artifact() -> None:
    raw = (
        files("governance.integrations.odcs.schemas")
        .joinpath("odcs-json-schema-v3.1.0.json")
        .read_bytes()
    )
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == ODCS_SCHEMA_SHA256
    assert digest == "2cb7dd6fe43344d2233e0406438622681dc3ebadcf8f0d606a15b40c8f6752c0"


def test_package_version_remains_110() -> None:
    assert __version__ == "1.1.0"
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert 'version = "1.1.0"' in pyproject.read_text(encoding="utf-8")


def test_existing_four_schemas_still_packaged() -> None:
    assert (
        files("governance.config_contract.schemas")
        .joinpath("governance-config.v1.schema.json")
        .is_file()
    )
    assert files("governance.policy.schemas").joinpath("governance-policy.v1.schema.json").is_file()
    assert files("governance.plans.schemas").joinpath("governance-plan.v1.schema.json").is_file()
    assert (
        files("governance.github_ci.schemas")
        .joinpath("governance-action-result.v1.schema.json")
        .is_file()
    )


def test_third_party_notices_present() -> None:
    notices = Path(__file__).resolve().parents[1] / "THIRD_PARTY_NOTICES.md"
    text = notices.read_text(encoding="utf-8")
    assert "Open Data Contract Standard" in text
    assert "ODCS-SCHEMA-NOTICE.txt" in text

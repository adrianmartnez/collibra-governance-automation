"""Unit tests for native governance policy parse/normalize/evaluate."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.config_contract import load_canonical_config
from governance.domain import (
    Column,
    Database,
    DataSource,
    ForeignKey,
    GovernanceModel,
    Ownership,
    Relationship,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_foreign_key_id,
    make_relationship_id,
    make_schema_id,
    make_table_id,
)
from governance.policy import (
    NormalizedPolicy,
    NormalizedPolicySet,
    PolicyParseError,
    PolicySemanticError,
    evaluate_policies,
    load_normalized_policies,
)
from governance.policy.errors import CODE_DUPLICATE, CODE_MISSING, CODE_PARSE
from governance.policy.models import PolicySelector
from governance.policy.parse import parse_policy_yaml
from governance.policy.schema import validate_policy_structure

POLICY_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "policies"
CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _write_config(
    tmp_path: Path,
    *,
    policy_files: list[str],
    copy_from: Path | None = None,
) -> Path:
    policies_dir = tmp_path / "policies"
    policies_dir.mkdir(exist_ok=True)
    for name in policy_files:
        src = (copy_from or POLICY_FIXTURES) / Path(name).name
        if not src.is_file():
            src = POLICY_FIXTURES / Path(name).name
        (policies_dir / Path(name).name).write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    mapping = CONFIG_FIXTURES / "mapping.json"
    (tmp_path / "mapping.json").write_text(mapping.read_text(encoding="utf-8"), encoding="utf-8")
    if policy_files:
        files_block = "  files:\n" + "\n".join(
            f"    - policies/{Path(name).name}" for name in policy_files
        )
    else:
        files_block = "  files: []"
    path = tmp_path / "governance.yaml"
    path.write_text(
        "\n".join(
            [
                'schema_version: "1"',
                "sources:",
                "  - id: primary",
                "    provider: postgresql",
                "    config:",
                "      source_name: governance-demo",
                "      connection:",
                "        database_url_env: DATABASE_URL",
                "targets:",
                "  - id: collibra",
                "    provider: collibra",
                "    config:",
                "      mode_env: COLLIBRA_MODE",
                "      mapping:",
                "        path: mapping.json",
                "      auth:",
                "        base_url_env: COLLIBRA_BASE_URL",
                "policies:",
                files_block,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


_DEFAULT_OWNER = Ownership(owner_name="owner")


def _model(
    *,
    customers_owner: Ownership | None = _DEFAULT_OWNER,
    orders_owner: Ownership | None = _DEFAULT_OWNER,
    customers_description: str | None = "customers table",
    orders_description: str | None = "orders table",
    include_relationship: bool = True,
) -> GovernanceModel:
    source = "governance-demo"
    database = "governance_demo"
    schema = "commerce"
    customers_id = make_table_id(source, database, schema, "customers")
    orders_id = make_table_id(source, database, schema, "orders")
    customers_col = make_column_id(source, database, schema, "customers", "customer_id")
    orders_col = make_column_id(source, database, schema, "orders", "order_id")
    orders_fk_col = make_column_id(source, database, schema, "orders", "customer_id")
    fk_id = make_foreign_key_id(orders_id, "orders_customer_fkey")
    relationships = ()
    if include_relationship:
        relationships = (
            Relationship(
                id=make_relationship_id(fk_id),
                name="orders_customer_fkey",
                from_table_id=orders_id,
                to_table_id=customers_id,
                foreign_key_id=fk_id,
            ),
        )
    return GovernanceModel(
        data_sources=(
            DataSource(
                id=make_datasource_id(source),
                name=source,
                system_type="postgresql",
                databases=(
                    Database(
                        id=make_database_id(source, database),
                        name=database,
                        datasource_id=make_datasource_id(source),
                        schemas=(
                            Schema(
                                id=make_schema_id(source, database, schema),
                                name=schema,
                                database_id=make_database_id(source, database),
                                tables=(
                                    Table(
                                        id=customers_id,
                                        name="customers",
                                        schema_id=make_schema_id(source, database, schema),
                                        description=customers_description,
                                        ownership=customers_owner,
                                        columns=(
                                            Column(
                                                id=customers_col,
                                                name="customer_id",
                                                data_type="uuid",
                                                ordinal_position=1,
                                                nullable=False,
                                            ),
                                        ),
                                    ),
                                    Table(
                                        id=orders_id,
                                        name="orders",
                                        schema_id=make_schema_id(source, database, schema),
                                        description=orders_description,
                                        ownership=orders_owner,
                                        columns=(
                                            Column(
                                                id=orders_col,
                                                name="order_id",
                                                data_type="bigint",
                                                ordinal_position=1,
                                                nullable=False,
                                            ),
                                            Column(
                                                id=orders_fk_col,
                                                name="customer_id",
                                                data_type="uuid",
                                                ordinal_position=2,
                                                nullable=False,
                                            ),
                                        ),
                                        foreign_keys=(
                                            ForeignKey(
                                                id=fk_id,
                                                name="orders_customer_fkey",
                                                table_id=orders_id,
                                                column_ids=(orders_fk_col,),
                                                referenced_table_id=customers_id,
                                                referenced_column_ids=(customers_col,),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        relationships=relationships,
    )


def test_parse_valid_fixture() -> None:
    document = parse_policy_yaml(
        POLICY_FIXTURES / "tables_require_owner.yaml",
        source="tables_require_owner.yaml",
    )
    validate_policy_structure(document, source="tables_require_owner.yaml")
    assert document["policy_schema"] == "governance-policy"
    assert document["policies"][0]["rule"]["type"] == "require_owner"


def test_parse_malformed_yaml() -> None:
    with pytest.raises(PolicyParseError) as exc:
        parse_policy_yaml(POLICY_FIXTURES / "malformed.yaml", source="malformed.yaml")
    assert exc.value.errors[0].code == CODE_PARSE


def test_load_and_evaluate_require_owner(tmp_path: Path) -> None:
    config = _write_config(tmp_path, policy_files=["tables_require_owner.yaml"])
    policy_set = load_normalized_policies(load_canonical_config(config))
    assert len(policy_set.policies) == 1
    assert policy_set.policies[0].rule_type == "require_owner"
    assert policy_set.policies[0].severity == "error"

    violations = evaluate_policies(
        _model(customers_owner=None, orders_owner=None),
        policy_set,
    )
    assert len(violations) == 2
    assert all(item.rule_type == "require_owner" for item in violations)
    assert all(item.severity == "error" for item in violations)
    assert {item.object_name for item in violations} == {"customers", "orders"}


def test_evaluate_require_description_warning(tmp_path: Path) -> None:
    config = _write_config(tmp_path, policy_files=["tables_require_description_warning.yaml"])
    policy_set = load_normalized_policies(load_canonical_config(config))
    violations = evaluate_policies(
        _model(customers_description=None, orders_description="  "),
        policy_set,
    )
    assert len(violations) == 2
    assert all(item.severity == "warning" for item in violations)
    assert all(item.rule_type == "require_description" for item in violations)


def test_evaluate_require_relationship() -> None:
    policy_set = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="tables-require-relationship",
                severity="error",
                rule_type="require_relationship",
                select=PolicySelector(kind="table"),
            ),
        )
    )
    violations = evaluate_policies(_model(include_relationship=False), policy_set)
    assert len(violations) == 2
    assert all(item.rule_type == "require_relationship" for item in violations)

    clean = evaluate_policies(_model(include_relationship=True), policy_set)
    assert clean == ()


def test_selectors_id_and_prefix() -> None:
    model = _model(customers_owner=None, orders_owner=None)
    customers_id = make_table_id("governance-demo", "governance_demo", "commerce", "customers")
    by_id = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="one-table",
                severity="error",
                rule_type="require_owner",
                select=PolicySelector(kind="table", object_id=customers_id),
            ),
        )
    )
    violations = evaluate_policies(model, by_id)
    assert len(violations) == 1
    assert violations[0].object_id == customers_id

    by_prefix = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="prefix",
                severity="error",
                rule_type="require_owner",
                select=PolicySelector(
                    kind="table",
                    id_prefix="tbl:governance-demo/governance_demo/commerce/",
                ),
            ),
        )
    )
    assert len(evaluate_policies(model, by_prefix)) == 2


def test_duplicate_policy_ids_rejected(tmp_path: Path) -> None:
    body = (POLICY_FIXTURES / "tables_require_owner.yaml").read_text(encoding="utf-8")
    policies_dir = tmp_path / "policies"
    policies_dir.mkdir()
    (policies_dir / "a.yaml").write_text(body, encoding="utf-8")
    (policies_dir / "b.yaml").write_text(body, encoding="utf-8")
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / "governance.yaml"
    config.write_text(
        "\n".join(
            [
                'schema_version: "1"',
                "sources:",
                "  - id: primary",
                "    provider: postgresql",
                "    config:",
                "      source_name: governance-demo",
                "      connection:",
                "        database_url_env: DATABASE_URL",
                "targets:",
                "  - id: collibra",
                "    provider: collibra",
                "    config:",
                "      mode_env: COLLIBRA_MODE",
                "      mapping:",
                "        path: mapping.json",
                "policies:",
                "  files:",
                "    - policies/a.yaml",
                "    - policies/b.yaml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicySemanticError) as exc:
        load_normalized_policies(load_canonical_config(config))
    assert any(error.code == CODE_DUPLICATE for error in exc.value.errors)


def test_severity_orders_errors_before_warnings() -> None:
    policy_set = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="warn-desc",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(kind="table"),
            ),
            NormalizedPolicy(
                id="err-owner",
                severity="error",
                rule_type="require_owner",
                select=PolicySelector(kind="table"),
            ),
        )
    )
    violations = evaluate_policies(
        _model(
            customers_owner=None,
            orders_owner=None,
            customers_description=None,
            orders_description=None,
        ),
        policy_set,
    )
    assert violations
    assert violations[0].severity == "error"
    error_indexes = [i for i, item in enumerate(violations) if item.severity == "error"]
    warning_indexes = [i for i, item in enumerate(violations) if item.severity == "warning"]
    assert error_indexes and warning_indexes
    assert max(error_indexes) < min(warning_indexes)


def test_missing_policy_file(tmp_path: Path) -> None:
    config = _write_config(tmp_path, policy_files=[])
    text = config.read_text(encoding="utf-8").replace(
        "files: []",
        "files:\n    - policies/does-not-exist.yaml",
    )
    config.write_text(text, encoding="utf-8")
    with pytest.raises(PolicySemanticError) as exc:
        load_normalized_policies(load_canonical_config(config))
    assert any(error.code == CODE_MISSING for error in exc.value.errors)


def test_empty_policy_set_evaluates_clean(tmp_path: Path) -> None:
    config = _write_config(tmp_path, policy_files=[])
    policy_set = load_normalized_policies(load_canonical_config(config))
    assert policy_set.policies == ()
    assert evaluate_policies(_model(customers_owner=None), policy_set) == ()

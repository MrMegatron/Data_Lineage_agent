import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app import models  # noqa: F401
from app.database import Base, engine


# ============================================================
# 1. 预期的8张核心业务表
# ============================================================

EXPECTED_TABLES = {
    "lineage_project",
    "source_script",
    "data_table",
    "data_column",
    "script_table_access",
    "script_dependency",
    "column_lineage",
    "lineage_evidence",
}


# ============================================================
# 2. 预期的15个外键
# ============================================================

EXPECTED_FOREIGN_KEYS = {
    "source_script": {
        (
            "project_id",
            "lineage_project.id",
            "CASCADE",
        ),
    },

    "data_table": {
        (
            "project_id",
            "lineage_project.id",
            "CASCADE",
        ),
    },

    "data_column": {
        (
            "table_id",
            "data_table.id",
            "CASCADE",
        ),
    },

    "script_table_access": {
        (
            "script_id",
            "source_script.id",
            "CASCADE",
        ),
        (
            "table_id",
            "data_table.id",
            "CASCADE",
        ),
    },

    "script_dependency": {
        (
            "project_id",
            "lineage_project.id",
            "CASCADE",
        ),
        (
            "upstream_script_id",
            "source_script.id",
            "CASCADE",
        ),
        (
            "downstream_script_id",
            "source_script.id",
            "CASCADE",
        ),
        (
            "via_table_id",
            "data_table.id",
            "CASCADE",
        ),
    },

    "column_lineage": {
        (
            "project_id",
            "lineage_project.id",
            "CASCADE",
        ),
        (
            "script_id",
            "source_script.id",
            "CASCADE",
        ),
        (
            "target_column_id",
            "data_column.id",
            "CASCADE",
        ),
        (
            "source_column_id",
            "data_column.id",
            "CASCADE",
        ),
    },

    "lineage_evidence": {
        (
            "column_lineage_id",
            "column_lineage.id",
            "CASCADE",
        ),
        (
            "script_id",
            "source_script.id",
            "CASCADE",
        ),
    },
}


# ============================================================
# 3. 预期的唯一约束
# ============================================================

EXPECTED_UNIQUE_CONSTRAINTS = {
    "source_script": {
        frozenset({
            "project_id",
            "relative_path",
        }),
    },

    "data_table": {
        frozenset({
            "project_id",
            "full_name",
        }),
    },

    "data_column": {
        frozenset({
            "table_id",
            "column_name",
        }),
    },

    "script_table_access": {
        frozenset({
            "script_id",
            "table_id",
            "access_type",
            "statement_no",
        }),
    },

    "script_dependency": {
        frozenset({
            "project_id",
            "upstream_script_id",
            "downstream_script_id",
            "via_table_id",
        }),
    },
}


# ============================================================
# 4. 预期的关键索引
# ============================================================

EXPECTED_INDEXES = {
    "script_table_access": {
        frozenset({
            "table_id",
            "access_type",
        }),
    },

    "script_dependency": {
        frozenset({
            "downstream_script_id",
            "dependency_status",
        }),
    },

    "column_lineage": {
        frozenset({
            "target_column_id",
            "resolution_status",
        }),
        frozenset({
            "source_column_id",
        }),
    },

    "lineage_evidence": {
        frozenset({
            "column_lineage_id",
            "evidence_order",
        }),
    },
}


# ============================================================
# 5. 测试8张核心表是否存在
# ============================================================

@pytest.mark.integration
def test_all_core_tables_exist() -> None:
    inspector = inspect(engine)

    actual_tables = set(
        inspector.get_table_names()
    )

    missing_tables = EXPECTED_TABLES - actual_tables

    assert not missing_tables, (
        "以下核心表不存在："
        f"{sorted(missing_tables)}"
    )


# ============================================================
# 6. 测试数据库字段与ORM字段是否一致
# ============================================================

@pytest.mark.integration
def test_database_columns_match_orm() -> None:
    inspector = inspect(engine)

    for table_name in sorted(EXPECTED_TABLES):
        orm_table = Base.metadata.tables[table_name]

        expected_columns = {
            column.name
            for column in orm_table.columns
        }

        actual_columns = {
            column["name"]
            for column in inspector.get_columns(
                table_name
            )
        }

        assert actual_columns == expected_columns, (
            f"\n表 {table_name} 的数据库字段与ORM不一致"
            f"\nORM字段：{sorted(expected_columns)}"
            f"\n数据库字段：{sorted(actual_columns)}"
            f"\n数据库缺少："
            f"{sorted(expected_columns - actual_columns)}"
            f"\n数据库多出："
            f"{sorted(actual_columns - expected_columns)}"
        )


# ============================================================
# 7. 测试所有核心表的主键
# ============================================================

@pytest.mark.integration
def test_all_primary_keys_are_id() -> None:
    inspector = inspect(engine)

    for table_name in sorted(EXPECTED_TABLES):
        primary_key = inspector.get_pk_constraint(
            table_name
        )

        actual_columns = set(
            primary_key.get(
                "constrained_columns",
                [],
            )
        )

        assert actual_columns == {"id"}, (
            f"表 {table_name} 的主键错误："
            f"实际={sorted(actual_columns)}"
        )


# ============================================================
# 8. 测试15个外键和CASCADE
# ============================================================

@pytest.mark.integration
def test_all_foreign_keys_and_cascade() -> None:
    inspector = inspect(engine)

    for table_name, expected_foreign_keys in (
        EXPECTED_FOREIGN_KEYS.items()
    ):
        reflected_foreign_keys = (
            inspector.get_foreign_keys(
                table_name
            )
        )

        actual_foreign_keys = set()

        for foreign_key in reflected_foreign_keys:
            local_columns = foreign_key.get(
                "constrained_columns",
                [],
            )

            remote_columns = foreign_key.get(
                "referred_columns",
                [],
            )

            referred_table = foreign_key.get(
                "referred_table"
            )

            options = foreign_key.get(
                "options",
                {},
            ) or {}

            ondelete = (
                options.get("ondelete", "")
                or ""
            ).upper()

            for local_column, remote_column in zip(
                local_columns,
                remote_columns,
            ):
                actual_foreign_keys.add(
                    (
                        local_column,
                        (
                            f"{referred_table}."
                            f"{remote_column}"
                        ),
                        ondelete,
                    )
                )

        assert actual_foreign_keys == expected_foreign_keys, (
            f"\n表 {table_name} 的外键不一致"
            f"\n预期：{sorted(expected_foreign_keys)}"
            f"\n实际：{sorted(actual_foreign_keys)}"
        )


# ============================================================
# 9. 测试关键唯一约束
# ============================================================

@pytest.mark.integration
def test_required_unique_constraints() -> None:
    inspector = inspect(engine)

    for table_name, expected_constraints in (
        EXPECTED_UNIQUE_CONSTRAINTS.items()
    ):
        reflected_constraints = (
            inspector.get_unique_constraints(
                table_name
            )
        )

        actual_constraints = {
            frozenset(
                constraint.get(
                    "column_names",
                    [],
                )
            )
            for constraint in reflected_constraints
        }

        assert expected_constraints <= actual_constraints, (
            f"\n表 {table_name} 缺少唯一约束"
            f"\n预期至少包含：{expected_constraints}"
            f"\n实际：{actual_constraints}"
        )


# ============================================================
# 10. 测试关键索引
# ============================================================

@pytest.mark.integration
def test_required_indexes() -> None:
    inspector = inspect(engine)

    for table_name, expected_indexes in (
        EXPECTED_INDEXES.items()
    ):
        reflected_indexes = inspector.get_indexes(
            table_name
        )

        actual_indexes = {
            frozenset(
                index.get(
                    "column_names",
                    [],
                )
            )
            for index in reflected_indexes
        }

        assert expected_indexes <= actual_indexes, (
            f"\n表 {table_name} 缺少关键索引"
            f"\n预期至少包含：{expected_indexes}"
            f"\n实际：{actual_indexes}"
        )


# ============================================================
# 11. 测试数据库revision是否等于Alembic head
# ============================================================

@pytest.mark.integration
def test_database_revision_is_alembic_head() -> None:
    alembic_config = Config(
        "alembic.ini"
    )

    script_directory = (
        ScriptDirectory.from_config(
            alembic_config
        )
    )

    expected_head = (
        script_directory.get_current_head()
    )

    with engine.connect() as connection:
        migration_context = (
            MigrationContext.configure(
                connection
            )
        )

        actual_revision = (
            migration_context
            .get_current_revision()
        )

    assert expected_head is not None

    assert actual_revision == expected_head, (
        "数据库revision不是Alembic head："
        f"代码head={expected_head}，"
        f"数据库revision={actual_revision}"
    )
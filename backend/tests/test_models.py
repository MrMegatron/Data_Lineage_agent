from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)


# ============================================================
# 1. 生成测试专用唯一名称
# ============================================================

def unique_name(prefix: str) -> str:
    """
    生成不重复的测试名称。

    示例：
        pytest_project_a1b2c3d4e5
    """

    return f"{prefix}_{uuid4().hex[:10]}"


# ============================================================
# 2. 测试 Project -> SourceScript
# ============================================================

@pytest.mark.integration
def test_create_project_and_script(
    db_session: Session,
) -> None:
    """
    验证：
    1. 可以创建项目；
    2. 可以创建属于该项目的脚本；
    3. MySQL能够生成自增ID；
    4. project_id外键正确；
    5. ORM relationship正确。
    """

    project = LineageProject(
        name=unique_name(
            "pytest_project"
        ),
        description=(
            "pytest project and script test"
        ),
    )

    db_session.add(project)
    db_session.flush()

    script = SourceScript(
        project_id=project.id,
        file_name="order_detail.sql",
        relative_path=(
            "models/dwd/order_detail.sql"
        ),
        dialect="hive",
        file_hash="a" * 64,
        source_code=(
            "SELECT * "
            "FROM ods.order_detail"
        ),
        parse_status="pending",
    )

    db_session.add(script)
    db_session.flush()

    assert project.id is not None
    assert script.id is not None

    assert script.project_id == project.id

    saved_script = db_session.scalar(
        select(SourceScript).where(
            SourceScript.id == script.id
        )
    )

    assert saved_script is not None
    assert saved_script.file_name == (
        "order_detail.sql"
    )
    assert saved_script.project.id == project.id
    assert saved_script.project.name == project.name


# ============================================================
# 3. 测试 DataTable -> DataColumn
# ============================================================

@pytest.mark.integration
def test_create_table_and_columns(
    db_session: Session,
) -> None:
    """
    验证：
    1. 可以创建数据表；
    2. 可以创建表字段；
    3. table_id外键正确；
    4. table.columns relationship正确。
    """

    project = LineageProject(
        name=unique_name(
            "pytest_table_project"
        ),
    )

    db_session.add(project)
    db_session.flush()

    data_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="order_detail",
        full_name="dwd.order_detail",
        table_kind="physical",
    )

    db_session.add(data_table)
    db_session.flush()

    order_id_column = DataColumn(
        table_id=data_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="string",
    )

    amount_column = DataColumn(
        table_id=data_table.id,
        column_name="amount",
        ordinal_position=2,
        data_type="decimal(18,2)",
    )

    db_session.add_all([
        order_id_column,
        amount_column,
    ])

    db_session.flush()

    assert data_table.id is not None
    assert order_id_column.id is not None
    assert amount_column.id is not None

    assert order_id_column.table_id == (
        data_table.id
    )

    saved_columns = db_session.scalars(
        select(DataColumn)
        .where(
            DataColumn.table_id
            == data_table.id
        )
        .order_by(
            DataColumn.ordinal_position
        )
    ).all()

    assert len(saved_columns) == 2

    assert [
        column.column_name
        for column in saved_columns
    ] == [
        "order_id",
        "amount",
    ]

    assert {
        column.column_name
        for column in data_table.columns
    } == {
        "order_id",
        "amount",
    }


# ============================================================
# 4. 测试脚本READ / WRITE表
# ============================================================

@pytest.mark.integration
def test_script_read_write(
    db_session: Session,
) -> None:
    """
    验证一个脚本能够：
    - READ ods.orders；
    - WRITE dwd.orders。
    """

    project = LineageProject(
        name=unique_name(
            "pytest_access_project"
        ),
    )

    db_session.add(project)
    db_session.flush()

    script = SourceScript(
        project_id=project.id,
        file_name="ods_to_dwd.sql",
        relative_path=(
            "models/ods_to_dwd.sql"
        ),
        dialect="hive",
        file_hash="b" * 64,
        source_code=(
            "INSERT INTO dwd.orders "
            "SELECT * FROM ods.orders"
        ),
        parse_status="success",
    )

    source_table = DataTable(
        project_id=project.id,
        schema_name="ods",
        table_name="orders",
        full_name="ods.orders",
        table_kind="physical",
    )

    target_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="physical",
    )

    db_session.add_all([
        script,
        source_table,
        target_table,
    ])

    db_session.flush()

    read_access = ScriptTableAccess(
        script_id=script.id,
        table_id=source_table.id,
        access_type="read",
        statement_no=1,
        evidence_sql="FROM ods.orders",
    )

    write_access = ScriptTableAccess(
        script_id=script.id,
        table_id=target_table.id,
        access_type="write",
        statement_no=1,
        evidence_sql=(
            "INSERT INTO dwd.orders"
        ),
    )

    db_session.add_all([
        read_access,
        write_access,
    ])

    db_session.flush()

    assert read_access.id is not None
    assert write_access.id is not None

    saved_accesses = db_session.scalars(
        select(ScriptTableAccess)
        .where(
            ScriptTableAccess.script_id
            == script.id
        )
        .order_by(
            ScriptTableAccess.access_type
        )
    ).all()

    assert len(saved_accesses) == 2

    actual_accesses = {
        (
            access.access_type,
            access.table.full_name,
        )
        for access in saved_accesses
    }

    assert actual_accesses == {
        (
            "read",
            "ods.orders",
        ),
        (
            "write",
            "dwd.orders",
        ),
    }


# ============================================================
# 5. 测试多上游歧义关系
# ============================================================

@pytest.mark.integration
def test_ambiguous_script_dependency(
    db_session: Session,
) -> None:
    """
    核心业务场景：

    script_a和script_b同时写入dwd.orders，
    script_c读取dwd.orders。

    系统必须允许保存：
        script_a -> script_c ambiguous
        script_b -> script_c ambiguous

    不能擅自选择唯一上游。
    """

    project = LineageProject(
        name=unique_name(
            "pytest_ambiguity_project"
        ),
    )

    db_session.add(project)
    db_session.flush()

    script_a = SourceScript(
        project_id=project.id,
        file_name="a.sql",
        relative_path="a.sql",
        dialect="hive",
        file_hash="a" * 64,
        source_code=(
            "INSERT INTO dwd.orders "
            "SELECT 1"
        ),
        parse_status="success",
    )

    script_b = SourceScript(
        project_id=project.id,
        file_name="b.sql",
        relative_path="b.sql",
        dialect="hive",
        file_hash="b" * 64,
        source_code=(
            "INSERT INTO dwd.orders "
            "SELECT 2"
        ),
        parse_status="success",
    )

    script_c = SourceScript(
        project_id=project.id,
        file_name="c.sql",
        relative_path="c.sql",
        dialect="hive",
        file_hash="c" * 64,
        source_code=(
            "SELECT * FROM dwd.orders"
        ),
        parse_status="success",
    )

    via_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="physical",
    )

    db_session.add_all([
        script_a,
        script_b,
        script_c,
        via_table,
    ])

    db_session.flush()

    dependency_a = ScriptDependency(
        project_id=project.id,
        upstream_script_id=script_a.id,
        downstream_script_id=script_c.id,
        via_table_id=via_table.id,
        dependency_status="ambiguous",
        reason=(
            "Multiple scripts write "
            "dwd.orders"
        ),
    )

    dependency_b = ScriptDependency(
        project_id=project.id,
        upstream_script_id=script_b.id,
        downstream_script_id=script_c.id,
        via_table_id=via_table.id,
        dependency_status="ambiguous",
        reason=(
            "Multiple scripts write "
            "dwd.orders"
        ),
    )

    db_session.add_all([
        dependency_a,
        dependency_b,
    ])

    db_session.flush()

    dependencies = db_session.scalars(
        select(ScriptDependency)
        .where(
            ScriptDependency.downstream_script_id
            == script_c.id
        )
    ).all()

    assert len(dependencies) == 2

    assert {
        dependency.upstream_script_id
        for dependency in dependencies
    } == {
        script_a.id,
        script_b.id,
    }

    assert all(
        dependency.dependency_status
        == "ambiguous"
        for dependency in dependencies
    )

    assert all(
        dependency.via_table_id
        == via_table.id
        for dependency in dependencies
    )


# ============================================================
# 6. 测试字段血缘和源码证据
# ============================================================

@pytest.mark.integration
def test_column_lineage_with_evidence(
    db_session: Session,
) -> None:
    """
    测试完整字段血缘：

        price
          \\
           -> amount
          /
        quantity

    转换表达式：
        price * quantity

    同时保存代码证据。
    """

    project = LineageProject(
        name=unique_name(
            "pytest_lineage_project"
        ),
    )

    db_session.add(project)
    db_session.flush()

    script = SourceScript(
        project_id=project.id,
        file_name="order_detail.sql",
        relative_path=(
            "models/order_detail.sql"
        ),
        dialect="hive",
        file_hash="d" * 64,
        source_code=(
            "INSERT INTO dwd.order_detail "
            "SELECT "
            "price * quantity AS amount "
            "FROM ods.order_detail"
        ),
        parse_status="success",
    )

    source_table = DataTable(
        project_id=project.id,
        schema_name="ods",
        table_name="order_detail",
        full_name="ods.order_detail",
        table_kind="physical",
    )

    target_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="order_detail",
        full_name="dwd.order_detail",
        table_kind="physical",
    )

    db_session.add_all([
        script,
        source_table,
        target_table,
    ])

    db_session.flush()

    price_column = DataColumn(
        table_id=source_table.id,
        column_name="price",
        ordinal_position=1,
        data_type="decimal(18,2)",
    )

    quantity_column = DataColumn(
        table_id=source_table.id,
        column_name="quantity",
        ordinal_position=2,
        data_type="int",
    )

    amount_column = DataColumn(
        table_id=target_table.id,
        column_name="amount",
        ordinal_position=1,
        data_type="decimal(18,2)",
    )

    db_session.add_all([
        price_column,
        quantity_column,
        amount_column,
    ])

    db_session.flush()

    price_lineage = ColumnLineage(
        project_id=project.id,
        script_id=script.id,
        target_column_id=amount_column.id,
        source_column_id=price_column.id,
        relation_type="transform",
        resolution_status="confirmed",
        expression_text="price * quantity",
        statement_no=1,
    )

    quantity_lineage = ColumnLineage(
        project_id=project.id,
        script_id=script.id,
        target_column_id=amount_column.id,
        source_column_id=quantity_column.id,
        relation_type="transform",
        resolution_status="confirmed",
        expression_text="price * quantity",
        statement_no=1,
    )

    db_session.add_all([
        price_lineage,
        quantity_lineage,
    ])

    db_session.flush()

    evidence = LineageEvidence(
        column_lineage_id=price_lineage.id,
        script_id=script.id,
        statement_no=1,
        evidence_order=1,
        line_start=1,
        line_end=1,
        code_snippet=(
            "price * quantity AS amount"
        ),
        expression_text=(
            "price * quantity"
        ),
    )

    db_session.add(evidence)
    db_session.flush()

    saved_lineages = db_session.scalars(
        select(ColumnLineage)
        .where(
            ColumnLineage.target_column_id
            == amount_column.id
        )
    ).all()

    assert len(saved_lineages) == 2

    actual_source_ids = {
        lineage.source_column_id
        for lineage in saved_lineages
    }

    assert actual_source_ids == {
        price_column.id,
        quantity_column.id,
    }

    assert all(
        lineage.expression_text
        == "price * quantity"
        for lineage in saved_lineages
    )

    assert evidence.id is not None

    saved_evidence = db_session.scalar(
        select(LineageEvidence).where(
            LineageEvidence.id
            == evidence.id
        )
    )

    assert saved_evidence is not None

    assert saved_evidence.code_snippet == (
        "price * quantity AS amount"
    )

    assert (
        saved_evidence
        .column_lineage
        .target_column_id
        == amount_column.id
    )

    assert saved_evidence.script_id == script.id
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
    SourceScript,
)
from app.services.column_lineage_extractor import (
    extract_direct_column_lineage,
)
from app.services.column_lineage_persistence_service import (
    persist_direct_column_lineage,
)


# ============================================================
# 1. 创建测试基础数据
# ============================================================

def create_test_context(
    db_session,
):
    """
    创建：

        Project
        SourceScript
        ods.orders
        dwd.orders
    """

    unique_suffix = uuid4().hex[:8]

    project = LineageProject(
        name=(
            "column_lineage_test_"
            f"{unique_suffix}"
        ),
        description=(
            "字段血缘持久化测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    sql_text = """
    INSERT INTO dwd.orders (
        order_id,
        customer_id,
        amount
    )
    SELECT
        order_id,
        customer_id,
        amount
    FROM ods.orders;
    """

    script = SourceScript(
        project_id=project.id,
        file_name="ods_to_dwd.sql",
        relative_path=(
            f"tests/{unique_suffix}/"
            "ods_to_dwd.sql"
        ),
        dialect="hive",
        file_hash=uuid4().hex * 2,
        source_code=sql_text,
        parse_status="success",
        parse_error=None,
    )

    source_table = DataTable(
        project_id=project.id,
        catalog_name=None,
        schema_name="ods",
        table_name="orders",
        full_name="ods.orders",
        table_kind="physical",
    )

    target_table = DataTable(
        project_id=project.id,
        catalog_name=None,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="physical",
    )

    db_session.add_all(
        [
            script,
            source_table,
            target_table,
        ]
    )

    db_session.flush()

    return (
        project,
        script,
        source_table,
        target_table,
        sql_text,
    )


# ============================================================
# 2. 测试保存字段、血缘和证据
# ============================================================

def test_persist_direct_column_lineage(
    db_session,
):
    (
        project,
        script,
        source_table,
        target_table,
        sql_text,
    ) = create_test_context(
        db_session
    )

    extraction_result = (
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )
    )

    persistence_result = (
        persist_direct_column_lineage(
            db=db_session,
            project_id=project.id,
            script_id=script.id,
            statement_no=1,
            extraction_result=(
                extraction_result
            ),
        )
    )

    # 来源表三个字段
    # +
    # 目标表三个字段
    assert (
        persistence_result
        .created_column_count
        == 6
    )

    assert (
        persistence_result
        .reused_column_count
        == 0
    )

    assert (
        persistence_result
        .created_lineage_count
        == 3
    )

    assert (
        persistence_result
        .created_evidence_count
        == 3
    )

    assert (
        persistence_result
        .deleted_lineage_count
        == 0
    )

    # --------------------------------------------------------
    # 检查 DataColumn
    # --------------------------------------------------------

    columns = list(
        db_session.scalars(
            select(DataColumn)
            .where(
                DataColumn.table_id.in_(
                    [
                        source_table.id,
                        target_table.id,
                    ]
                )
            )
        ).all()
    )

    assert len(columns) == 6

    source_column_names = {
        column.column_name
        for column in columns
        if (
            column.table_id
            == source_table.id
        )
    }

    assert source_column_names == {
        "order_id",
        "customer_id",
        "amount",
    }

    target_columns = {
        column.column_name:
            column.ordinal_position
        for column in columns
        if (
            column.table_id
            == target_table.id
        )
    }

    assert target_columns == {
        "order_id": 1,
        "customer_id": 2,
        "amount": 3,
    }

    # --------------------------------------------------------
    # 检查 ColumnLineage
    # --------------------------------------------------------

    lineages = list(
        db_session.scalars(
            select(ColumnLineage)
            .where(
                ColumnLineage.project_id
                == project.id,
                ColumnLineage.script_id
                == script.id,
            )
            .order_by(
                ColumnLineage.id
            )
        ).all()
    )

    assert len(lineages) == 3

    assert all(
        lineage.relation_type
        == "direct"
        for lineage in lineages
    )

    assert all(
        lineage.resolution_status
        == "confirmed"
        for lineage in lineages
    )

    assert all(
        lineage.statement_no == 1
        for lineage in lineages
    )

    # --------------------------------------------------------
    # 检查 LineageEvidence
    # --------------------------------------------------------

    evidences = list(
        db_session.scalars(
            select(LineageEvidence)
            .where(
                LineageEvidence.script_id
                == script.id
            )
        ).all()
    )

    assert len(evidences) == 3

    evidence_snippets = {
        evidence.code_snippet
        for evidence in evidences
    }

    assert evidence_snippets == {
        "order_id",
        "customer_id",
        "amount",
    }


# ============================================================
# 3. 测试重复执行不会重复创建字段和血缘
# ============================================================

def test_reprocessing_is_idempotent(
    db_session,
):
    (
        project,
        script,
        source_table,
        target_table,
        sql_text,
    ) = create_test_context(
        db_session
    )

    extraction_result = (
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )
    )

    first_result = (
        persist_direct_column_lineage(
            db=db_session,
            project_id=project.id,
            script_id=script.id,
            statement_no=1,
            extraction_result=(
                extraction_result
            ),
        )
    )

    second_result = (
        persist_direct_column_lineage(
            db=db_session,
            project_id=project.id,
            script_id=script.id,
            statement_no=1,
            extraction_result=(
                extraction_result
            ),
        )
    )

    assert (
        first_result.created_column_count
        == 6
    )

    # 第二次应该全部复用字段。
    assert (
        second_result.created_column_count
        == 0
    )

    assert (
        second_result.reused_column_count
        == 6
    )

    # 第二次执行前删除第一次的三条血缘。
    assert (
        second_result.deleted_lineage_count
        == 3
    )

    # 删除后重新生成三条。
    assert (
        second_result.created_lineage_count
        == 3
    )

    column_count = int(
        db_session.scalar(
            select(
                func.count(
                    DataColumn.id
                )
            )
            .where(
                DataColumn.table_id.in_(
                    [
                        source_table.id,
                        target_table.id,
                    ]
                )
            )
        )
        or 0
    )

    lineage_count = int(
        db_session.scalar(
            select(
                func.count(
                    ColumnLineage.id
                )
            )
            .where(
                ColumnLineage.project_id
                == project.id,
                ColumnLineage.script_id
                == script.id,
            )
        )
        or 0
    )

    evidence_count = int(
        db_session.scalar(
            select(
                func.count(
                    LineageEvidence.id
                )
            )
            .where(
                LineageEvidence.script_id
                == script.id,
            )
        )
        or 0
    )

    assert column_count == 6
    assert lineage_count == 3
    assert evidence_count == 3


# ============================================================
# 4. 来源表不存在时必须拒绝
# ============================================================

def test_reject_missing_source_table(
    db_session,
):
    (
        project,
        script,
        source_table,
        target_table,
        sql_text,
    ) = create_test_context(
        db_session
    )

    # 删除来源表，
    # 模拟表级解析数据不完整。
    db_session.delete(source_table)
    db_session.flush()

    extraction_result = (
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )
    )

    with pytest.raises(
        ValueError,
        match="来源表不存在",
    ):
        persist_direct_column_lineage(
            db=db_session,
            project_id=project.id,
            script_id=script.id,
            statement_no=1,
            extraction_result=(
                extraction_result
            ),
        )


# ============================================================
# 5. 脚本属于其他项目时必须拒绝
# ============================================================

def test_reject_script_from_another_project(
    db_session,
):
    (
        project,
        script,
        source_table,
        target_table,
        sql_text,
    ) = create_test_context(
        db_session
    )

    another_project = LineageProject(
        name=(
            "another_project_"
            f"{uuid4().hex[:8]}"
        )
    )

    db_session.add(another_project)
    db_session.flush()

    extraction_result = (
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )
    )

    with pytest.raises(
        ValueError,
        match="不属于指定项目",
    ):
        persist_direct_column_lineage(
            db=db_session,
            project_id=another_project.id,
            script_id=script.id,
            statement_no=1,
            extraction_result=(
                extraction_result
            ),
        )


# ============================================================
# 6. statement_no 必须合法
# ============================================================

def test_reject_invalid_statement_number(
    db_session,
):
    (
        project,
        script,
        source_table,
        target_table,
        sql_text,
    ) = create_test_context(
        db_session
    )

    extraction_result = (
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )
    )

    with pytest.raises(
        ValueError,
        match="statement_no",
    ):
        persist_direct_column_lineage(
            db=db_session,
            project_id=project.id,
            script_id=script.id,
            statement_no=0,
            extraction_result=(
                extraction_result
            ),
        )
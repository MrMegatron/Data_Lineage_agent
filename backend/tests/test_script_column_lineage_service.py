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
from app.services.script_column_lineage_service import (
    process_script_column_lineage,
)


# ============================================================
# 1. 创建测试脚本和数据表
# ============================================================

def create_script_context(
    db_session,
    source_code: str,
):
    unique_suffix = uuid4().hex[:8]

    project = LineageProject(
        name=(
            "script_column_test_"
            f"{unique_suffix}"
        )
    )

    db_session.add(project)
    db_session.flush()

    script = SourceScript(
        project_id=project.id,
        file_name="ods_to_dwd.sql",
        relative_path=(
            f"tests/{unique_suffix}/"
            "ods_to_dwd.sql"
        ),
        dialect="hive",
        file_hash=uuid4().hex * 2,
        source_code=source_code,
        parse_status="success",
        parse_error=None,
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
    )


# ============================================================
# 2. 成功处理一条直接字段映射语句
# ============================================================

def test_process_direct_column_lineage(
    db_session,
):
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

    (
        project,
        script,
        source_table,
        target_table,
    ) = create_script_context(
        db_session,
        sql_text,
    )

    result = process_script_column_lineage(
        db=db_session,
        script_id=script.id,
    )

    assert result.total_statement_count == 1
    assert result.success_statement_count == 1
    assert result.skipped_statement_count == 0
    assert result.failed_statement_count == 0

    assert result.created_column_count == 6
    assert result.created_lineage_count == 3
    assert result.created_evidence_count == 3

    assert len(result.statements) == 1
    assert result.statements[0].status == "success"

    lineage_count = int(
        db_session.scalar(
            select(
                func.count(
                    ColumnLineage.id
                )
            )
            .where(
                ColumnLineage.script_id
                == script.id
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
                == script.id
            )
        )
        or 0
    )

    assert lineage_count == 3
    assert evidence_count == 3


# ============================================================
# 3. 不支持的表达式只跳过，不抛出异常
# ============================================================

def test_skip_unsupported_transform_expression(
    db_session,
):
    sql_text = """
    INSERT INTO dwd.orders (
        amount
    )
    SELECT
        price * quantity
    FROM ods.orders;
    """

    (
        project,
        script,
        source_table,
        target_table,
    ) = create_script_context(
        db_session,
        sql_text,
    )

    result = process_script_column_lineage(
        db=db_session,
        script_id=script.id,
    )

    assert result.total_statement_count == 1
    assert result.success_statement_count == 0
    assert result.skipped_statement_count == 1
    assert result.failed_statement_count == 0

    statement_result = result.statements[0]

    assert statement_result.status == "skipped"

    assert (
        "只支持直接字段映射"
        in statement_result.reason
    )

    lineage_count = int(
        db_session.scalar(
            select(
                func.count(
                    ColumnLineage.id
                )
            )
            .where(
                ColumnLineage.script_id
                == script.id
            )
        )
        or 0
    )

    assert lineage_count == 0


# ============================================================
# 4. 测试多语句脚本
# ============================================================

def test_process_multiple_statements(
    db_session,
):
    sql_text = """
    INSERT INTO dwd.orders (
        order_id
    )
    SELECT
        order_id
    FROM ods.orders;

    SELECT
        order_id
    FROM ods.orders;
    """

    (
        project,
        script,
        source_table,
        target_table,
    ) = create_script_context(
        db_session,
        sql_text,
    )

    result = process_script_column_lineage(
        db=db_session,
        script_id=script.id,
    )

    assert result.total_statement_count == 2

    assert result.success_statement_count == 1
    assert result.skipped_statement_count == 1
    assert result.failed_statement_count == 0

    assert (
        result.statements[0].status
        == "success"
    )

    assert (
        result.statements[1].status
        == "skipped"
    )


# ============================================================
# 5. 重复处理不能产生重复记录
# ============================================================

def test_reprocessing_does_not_duplicate_records(
    db_session,
):
    sql_text = """
    INSERT INTO dwd.orders (
        order_id,
        amount
    )
    SELECT
        order_id,
        amount
    FROM ods.orders;
    """

    (
        project,
        script,
        source_table,
        target_table,
    ) = create_script_context(
        db_session,
        sql_text,
    )

    first_result = (
        process_script_column_lineage(
            db=db_session,
            script_id=script.id,
        )
    )

    second_result = (
        process_script_column_lineage(
            db=db_session,
            script_id=script.id,
        )
    )

    assert first_result.created_column_count == 4
    assert first_result.created_lineage_count == 2

    assert second_result.deleted_lineage_count == 2
    assert second_result.created_column_count == 0
    assert second_result.reused_column_count == 4
    assert second_result.created_lineage_count == 2

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
                ColumnLineage.script_id
                == script.id
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
                == script.id
            )
        )
        or 0
    )

    assert column_count == 4
    assert lineage_count == 2
    assert evidence_count == 2


# ============================================================
# 6. 脚本不存在
# ============================================================

def test_reject_missing_script(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="源码脚本不存在",
    ):
        process_script_column_lineage(
            db=db_session,
            script_id=-1,
        )
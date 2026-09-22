from uuid import uuid4

import pytest

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
    SourceScript,
)
from app.services.column_lineage_query_service import (
    get_column_lineage_detail,
)


# ============================================================
# 1. 创建完整的三层字段链路
# ============================================================

def create_three_level_lineage(
    db_session,
):
    """
    创建：

        ods.orders.order_id
                ↓
        dwd.orders.order_id
                ↓
        ads.order_report.order_id
    """

    unique_suffix = uuid4().hex[:8]

    project = LineageProject(
        name=(
            "column_query_test_"
            f"{unique_suffix}"
        )
    )

    db_session.add(project)
    db_session.flush()

    ods_table = DataTable(
        project_id=project.id,
        schema_name="ods",
        table_name="orders",
        full_name="ods.orders",
        table_kind="physical",
    )

    dwd_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="physical",
    )

    ads_table = DataTable(
        project_id=project.id,
        schema_name="ads",
        table_name="order_report",
        full_name="ads.order_report",
        table_kind="physical",
    )

    db_session.add_all(
        [
            ods_table,
            dwd_table,
            ads_table,
        ]
    )

    db_session.flush()

    ods_column = DataColumn(
        table_id=ods_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    dwd_column = DataColumn(
        table_id=dwd_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    ads_column = DataColumn(
        table_id=ads_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    db_session.add_all(
        [
            ods_column,
            dwd_column,
            ads_column,
        ]
    )

    db_session.flush()

    first_script = SourceScript(
        project_id=project.id,
        file_name="01_ods_to_dwd.sql",
        relative_path=(
            f"tests/{unique_suffix}/"
            "01_ods_to_dwd.sql"
        ),
        dialect="hive",
        file_hash="a" * 64,
        source_code=(
            "INSERT INTO dwd.orders "
            "(order_id) "
            "SELECT order_id "
            "FROM ods.orders"
        ),
        parse_status="success",
    )

    second_script = SourceScript(
        project_id=project.id,
        file_name="02_dwd_to_ads.sql",
        relative_path=(
            f"tests/{unique_suffix}/"
            "02_dwd_to_ads.sql"
        ),
        dialect="hive",
        file_hash="b" * 64,
        source_code=(
            "INSERT INTO ads.order_report "
            "(order_id) "
            "SELECT order_id "
            "FROM dwd.orders"
        ),
        parse_status="success",
    )

    db_session.add_all(
        [
            first_script,
            second_script,
        ]
    )

    db_session.flush()

    first_lineage = ColumnLineage(
        project_id=project.id,
        script_id=first_script.id,
        source_column_id=ods_column.id,
        target_column_id=dwd_column.id,
        relation_type="direct",
        resolution_status="confirmed",
        expression_text="order_id",
        statement_no=1,
    )

    second_lineage = ColumnLineage(
        project_id=project.id,
        script_id=second_script.id,
        source_column_id=dwd_column.id,
        target_column_id=ads_column.id,
        relation_type="direct",
        resolution_status="confirmed",
        expression_text="order_id",
        statement_no=1,
    )

    db_session.add_all(
        [
            first_lineage,
            second_lineage,
        ]
    )

    db_session.flush()

    first_evidence = LineageEvidence(
        column_lineage_id=first_lineage.id,
        script_id=first_script.id,
        statement_no=1,
        evidence_order=1,
        code_snippet="order_id",
        expression_text="order_id",
    )

    second_evidence = LineageEvidence(
        column_lineage_id=second_lineage.id,
        script_id=second_script.id,
        statement_no=1,
        evidence_order=1,
        code_snippet="order_id",
        expression_text="order_id",
    )

    db_session.add_all(
        [
            first_evidence,
            second_evidence,
        ]
    )

    db_session.flush()

    return (
        project,
        ods_column,
        dwd_column,
        ads_column,
        first_script,
        second_script,
    )


# ============================================================
# 2. 查询中间字段
# ============================================================

def test_query_middle_column(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        ads_column,
        first_script,
        second_script,
    ) = create_three_level_lineage(
        db_session
    )

    result = get_column_lineage_detail(
        db=db_session,
        column_id=dwd_column.id,
    )

    assert result.project_id == project.id

    assert (
        result.column.column_id
        == dwd_column.id
    )

    assert (
        result.column.table_full_name
        == "dwd.orders"
    )

    assert (
        result.column.column_name
        == "order_id"
    )

    assert result.upstream_count == 1
    assert result.downstream_count == 1

    # --------------------------------------------------------
    # 检查直接上游
    # --------------------------------------------------------

    upstream_edge = (
        result.upstream_edges[0]
    )

    assert (
        upstream_edge
        .source_column
        .table_full_name
        == "ods.orders"
    )

    assert (
        upstream_edge
        .source_column
        .column_name
        == "order_id"
    )

    assert (
        upstream_edge
        .target_column
        .table_full_name
        == "dwd.orders"
    )

    assert (
        upstream_edge.script_id
        == first_script.id
    )

    assert (
        upstream_edge.script_file_name
        == "01_ods_to_dwd.sql"
    )

    assert (
        upstream_edge.relation_type
        == "direct"
    )

    assert (
        upstream_edge.resolution_status
        == "confirmed"
    )

    assert (
        upstream_edge.evidences[0]
        .code_snippet
        == "order_id"
    )

    # --------------------------------------------------------
    # 检查直接下游
    # --------------------------------------------------------

    downstream_edge = (
        result.downstream_edges[0]
    )

    assert (
        downstream_edge
        .source_column
        .table_full_name
        == "dwd.orders"
    )

    assert (
        downstream_edge
        .target_column
        .table_full_name
        == "ads.order_report"
    )

    assert (
        downstream_edge.script_id
        == second_script.id
    )

    assert (
        downstream_edge.script_file_name
        == "02_dwd_to_ads.sql"
    )


# ============================================================
# 3. 查询最上游字段
# ============================================================

def test_query_root_source_column(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        ads_column,
        first_script,
        second_script,
    ) = create_three_level_lineage(
        db_session
    )

    result = get_column_lineage_detail(
        db=db_session,
        column_id=ods_column.id,
    )

    assert result.upstream_count == 0
    assert result.downstream_count == 1

    assert result.upstream_edges == ()

    assert (
        result.downstream_edges[0]
        .target_column
        .table_full_name
        == "dwd.orders"
    )


# ============================================================
# 4. 查询最终目标字段
# ============================================================

def test_query_final_target_column(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        ads_column,
        first_script,
        second_script,
    ) = create_three_level_lineage(
        db_session
    )

    result = get_column_lineage_detail(
        db=db_session,
        column_id=ads_column.id,
    )

    assert result.upstream_count == 1
    assert result.downstream_count == 0

    assert result.downstream_edges == ()

    assert (
        result.upstream_edges[0]
        .source_column
        .table_full_name
        == "dwd.orders"
    )


# ============================================================
# 5. 查询不存在的字段
# ============================================================

def test_reject_missing_column(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="数据字段不存在",
    ):
        get_column_lineage_detail(
            db=db_session,
            column_id=-1,
        )
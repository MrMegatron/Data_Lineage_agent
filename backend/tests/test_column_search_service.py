from uuid import uuid4

import pytest

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageProject,
    SourceScript,
)
from app.services.column_search_service import (
    search_project_columns,
)


# ============================================================
# 1. 创建字段搜索测试数据
# ============================================================

def create_column_search_data(
    db_session,
):
    """
    创建：

        ods.orders.order_id
        ods.orders.amount

        dwd.orders.order_id
        dwd.orders.amount

        ads.order_report.order_id

    血缘：

        ods.order_id
            -> dwd.order_id
            -> ads.order_id

        ods.amount
            -> dwd.amount
    """

    unique_suffix = uuid4().hex[:8]

    project = LineageProject(
        name=(
            "column_search_test_"
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

    ods_order_id = DataColumn(
        table_id=ods_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    ods_amount = DataColumn(
        table_id=ods_table.id,
        column_name="amount",
        ordinal_position=2,
        data_type="decimal",
    )

    dwd_order_id = DataColumn(
        table_id=dwd_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    dwd_amount = DataColumn(
        table_id=dwd_table.id,
        column_name="amount",
        ordinal_position=2,
        data_type="decimal",
    )

    ads_order_id = DataColumn(
        table_id=ads_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    db_session.add_all(
        [
            ods_order_id,
            ods_amount,
            dwd_order_id,
            dwd_amount,
            ads_order_id,
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
        file_hash="e" * 64,
        source_code=(
            "INSERT INTO dwd.orders "
            "(order_id, amount) "
            "SELECT order_id, amount "
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
        file_hash="f" * 64,
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

    lineages = [
        ColumnLineage(
            project_id=project.id,
            script_id=first_script.id,
            source_column_id=(
                ods_order_id.id
            ),
            target_column_id=(
                dwd_order_id.id
            ),
            relation_type="direct",
            resolution_status="confirmed",
            expression_text="order_id",
            statement_no=1,
        ),
        ColumnLineage(
            project_id=project.id,
            script_id=first_script.id,
            source_column_id=(
                ods_amount.id
            ),
            target_column_id=(
                dwd_amount.id
            ),
            relation_type="direct",
            resolution_status="confirmed",
            expression_text="amount",
            statement_no=1,
        ),
        ColumnLineage(
            project_id=project.id,
            script_id=second_script.id,
            source_column_id=(
                dwd_order_id.id
            ),
            target_column_id=(
                ads_order_id.id
            ),
            relation_type="direct",
            resolution_status="confirmed",
            expression_text="order_id",
            statement_no=1,
        ),
    ]

    db_session.add_all(lineages)
    db_session.flush()

    return (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    )


# ============================================================
# 2. 查询项目全部字段
# ============================================================

def test_search_all_project_columns(
    db_session,
):
    (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    ) = create_column_search_data(
        db_session
    )

    result = search_project_columns(
        db=db_session,
        project_id=project.id,
        page=1,
        page_size=20,
    )

    assert result.project_id == project.id
    assert result.project_name == project.name

    assert result.total == 5
    assert len(result.items) == 5


# ============================================================
# 3. 按表名搜索
# ============================================================

def test_search_columns_by_table_name(
    db_session,
):
    (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    ) = create_column_search_data(
        db_session
    )

    result = search_project_columns(
        db=db_session,
        project_id=project.id,
        table_name="orders",
    )

    # ods.orders 两个字段
    # dwd.orders 两个字段
    assert result.total == 4

    assert {
        item.table_full_name
        for item in result.items
    } == {
        "ods.orders",
        "dwd.orders",
    }


# ============================================================
# 4. 按字段名搜索
# ============================================================

def test_search_columns_by_column_name(
    db_session,
):
    (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    ) = create_column_search_data(
        db_session
    )

    result = search_project_columns(
        db=db_session,
        project_id=project.id,
        column_name="order_id",
    )

    assert result.total == 3

    assert all(
        item.column_name == "order_id"
        for item in result.items
    )


# ============================================================
# 5. 同时按表名和字段名搜索
# ============================================================

def test_search_by_table_and_column(
    db_session,
):
    (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    ) = create_column_search_data(
        db_session
    )

    result = search_project_columns(
        db=db_session,
        project_id=project.id,
        table_name="dwd.orders",
        column_name="amount",
    )

    assert result.total == 1
    assert len(result.items) == 1

    item = result.items[0]

    assert (
        item.table_full_name
        == "dwd.orders"
    )

    assert item.column_name == "amount"

    assert (
        item.upstream_lineage_count
        == 1
    )

    assert (
        item.downstream_lineage_count
        == 0
    )


# ============================================================
# 6. 检查中间字段上下游数量
# ============================================================

def test_search_result_lineage_counts(
    db_session,
):
    (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    ) = create_column_search_data(
        db_session
    )

    result = search_project_columns(
        db=db_session,
        project_id=project.id,
        table_name="dwd.orders",
        column_name="order_id",
    )

    assert result.total == 1

    item = result.items[0]

    assert (
        item.column_id
        == dwd_order_id.id
    )

    assert (
        item.upstream_lineage_count
        == 1
    )

    assert (
        item.downstream_lineage_count
        == 1
    )


# ============================================================
# 7. 全局关键字搜索
# ============================================================

def test_search_columns_by_keyword(
    db_session,
):
    (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    ) = create_column_search_data(
        db_session
    )

    result = search_project_columns(
        db=db_session,
        project_id=project.id,
        keyword="order_report",
    )

    assert result.total == 1

    assert (
        result.items[0]
        .table_full_name
        == "ads.order_report"
    )


# ============================================================
# 8. 测试分页
# ============================================================

def test_search_columns_pagination(
    db_session,
):
    (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    ) = create_column_search_data(
        db_session
    )

    first_page = search_project_columns(
        db=db_session,
        project_id=project.id,
        page=1,
        page_size=2,
    )

    second_page = search_project_columns(
        db=db_session,
        project_id=project.id,
        page=2,
        page_size=2,
    )

    third_page = search_project_columns(
        db=db_session,
        project_id=project.id,
        page=3,
        page_size=2,
    )

    assert first_page.total == 5
    assert len(first_page.items) == 2
    assert len(second_page.items) == 2
    assert len(third_page.items) == 1

    first_page_ids = {
        item.column_id
        for item in first_page.items
    }

    second_page_ids = {
        item.column_id
        for item in second_page.items
    }

    assert first_page_ids.isdisjoint(
        second_page_ids
    )


# ============================================================
# 9. 项目不存在
# ============================================================

def test_search_columns_rejects_missing_project(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="血缘项目不存在",
    ):
        search_project_columns(
            db=db_session,
            project_id=-1,
        )


# ============================================================
# 10. 非法分页参数
# ============================================================

def test_search_columns_rejects_invalid_pagination(
    db_session,
):
    (
        project,
        ods_order_id,
        ods_amount,
        dwd_order_id,
        dwd_amount,
        ads_order_id,
    ) = create_column_search_data(
        db_session
    )

    with pytest.raises(
        ValueError,
        match="page",
    ):
        search_project_columns(
            db=db_session,
            project_id=project.id,
            page=0,
        )

    with pytest.raises(
        ValueError,
        match="page_size",
    ):
        search_project_columns(
            db=db_session,
            project_id=project.id,
            page_size=101,
        )
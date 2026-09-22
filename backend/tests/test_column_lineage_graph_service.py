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
    get_column_lineage_graph,
)


# ============================================================
# 1. 创建四层字段血缘
# ============================================================

def create_four_level_lineage(
    db_session,
):
    """
    创建：

        ods.orders.order_id
                ↓
        dwd.orders.order_id
                ↓
        dws.order_summary.order_id
                ↓
        ads.order_report.order_id
    """

    unique_suffix = uuid4().hex[:8]

    project = LineageProject(
        name=(
            "column_graph_test_"
            f"{unique_suffix}"
        )
    )

    db_session.add(project)
    db_session.flush()

    table_definitions = [
        (
            "ods",
            "orders",
            "ods.orders",
        ),
        (
            "dwd",
            "orders",
            "dwd.orders",
        ),
        (
            "dws",
            "order_summary",
            "dws.order_summary",
        ),
        (
            "ads",
            "order_report",
            "ads.order_report",
        ),
    ]

    tables = []

    for (
        schema_name,
        table_name,
        full_name,
    ) in table_definitions:
        table = DataTable(
            project_id=project.id,
            schema_name=schema_name,
            table_name=table_name,
            full_name=full_name,
            table_kind="physical",
        )

        db_session.add(table)
        tables.append(table)

    db_session.flush()

    columns = []

    for table in tables:
        column = DataColumn(
            table_id=table.id,
            column_name="order_id",
            ordinal_position=1,
            data_type="bigint",
        )

        db_session.add(column)
        columns.append(column)

    db_session.flush()

    scripts = []

    script_names = [
        "01_ods_to_dwd.sql",
        "02_dwd_to_dws.sql",
        "03_dws_to_ads.sql",
    ]

    for index, script_name in enumerate(
        script_names,
        start=1,
    ):
        script = SourceScript(
            project_id=project.id,
            file_name=script_name,
            relative_path=(
                f"tests/{unique_suffix}/"
                f"{script_name}"
            ),
            dialect="hive",
            file_hash=(
                str(index) * 64
            ),
            source_code=(
                "INSERT INTO target_table "
                "(order_id) "
                "SELECT order_id "
                "FROM source_table"
            ),
            parse_status="success",
        )

        db_session.add(script)
        scripts.append(script)

    db_session.flush()

    lineages = []

    for index in range(3):
        lineage = ColumnLineage(
            project_id=project.id,
            script_id=scripts[index].id,
            source_column_id=(
                columns[index].id
            ),
            target_column_id=(
                columns[index + 1].id
            ),
            relation_type="direct",
            resolution_status="confirmed",
            expression_text="order_id",
            statement_no=1,
        )

        db_session.add(lineage)
        lineages.append(lineage)

    db_session.flush()

    for index, lineage in enumerate(
        lineages
    ):
        evidence = LineageEvidence(
            column_lineage_id=lineage.id,
            script_id=scripts[index].id,
            statement_no=1,
            evidence_order=1,
            code_snippet="order_id",
            expression_text="order_id",
        )

        db_session.add(evidence)

    db_session.flush()

    return (
        project,
        columns[0],  # ods
        columns[1],  # dwd
        columns[2],  # dws
        columns[3],  # ads
    )


# ============================================================
# 2. 从中间字段双向查询
# ============================================================

def test_query_both_directions(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        dws_column,
        ads_column,
    ) = create_four_level_lineage(
        db_session
    )

    result = get_column_lineage_graph(
        db=db_session,
        column_id=dws_column.id,
        direction="both",
        max_depth=10,
    )

    assert result.project_id == project.id
    assert result.direction == "both"

    # ods、dwd、dws、ads
    assert result.node_count == 4

    # 三条字段血缘
    assert result.edge_count == 3

    node_full_names = {
        (
            f"{node.table_full_name}."
            f"{node.column_name}"
        )
        for node in result.nodes
    }

    assert node_full_names == {
        "ods.orders.order_id",
        "dwd.orders.order_id",
        "dws.order_summary.order_id",
        "ads.order_report.order_id",
    }

    edge_depths = {
        (
            edge.lineage
            .source_column
            .table_full_name,
            edge.lineage
            .target_column
            .table_full_name,
        ): edge.depth
        for edge in result.edges
    }

    assert edge_depths[
        (
            "dwd.orders",
            "dws.order_summary",
        )
    ] == 1

    assert edge_depths[
        (
            "ods.orders",
            "dwd.orders",
        )
    ] == 2

    assert edge_depths[
        (
            "dws.order_summary",
            "ads.order_report",
        )
    ] == 1


# ============================================================
# 3. 向上查询并限制深度
# ============================================================

def test_query_upstream_with_depth_limit(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        dws_column,
        ads_column,
    ) = create_four_level_lineage(
        db_session
    )

    result = get_column_lineage_graph(
        db=db_session,
        column_id=ads_column.id,
        direction="upstream",
        max_depth=2,
    )

    assert result.direction == "upstream"

    # 根字段 ads
    # 第一层 dws
    # 第二层 dwd
    assert result.node_count == 3
    assert result.edge_count == 2

    node_tables = {
        node.table_full_name
        for node in result.nodes
    }

    assert node_tables == {
        "ads.order_report",
        "dws.order_summary",
        "dwd.orders",
    }

    # max_depth=2，因此不应包含 ods。
    assert "ods.orders" not in node_tables

    assert all(
        edge.traversal_direction
        == "upstream"
        for edge in result.edges
    )


# ============================================================
# 4. 从最上游向下查询
# ============================================================

def test_query_all_downstream(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        dws_column,
        ads_column,
    ) = create_four_level_lineage(
        db_session
    )

    result = get_column_lineage_graph(
        db=db_session,
        column_id=ods_column.id,
        direction="downstream",
        max_depth=10,
    )

    assert result.node_count == 4
    assert result.edge_count == 3

    assert all(
        edge.traversal_direction
        == "downstream"
        for edge in result.edges
    )

    depths = {
        edge.depth
        for edge in result.edges
    }

    assert depths == {
        1,
        2,
        3,
    }


# ============================================================
# 5. 最大深度为1
# ============================================================

def test_max_depth_one(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        dws_column,
        ads_column,
    ) = create_four_level_lineage(
        db_session
    )

    result = get_column_lineage_graph(
        db=db_session,
        column_id=dwd_column.id,
        direction="both",
        max_depth=1,
    )

    # 当前 dwd
    # 直接上游 ods
    # 直接下游 dws
    assert result.node_count == 3
    assert result.edge_count == 2

    assert all(
        edge.depth == 1
        for edge in result.edges
    )


# ============================================================
# 6. 非法方向
# ============================================================

def test_reject_invalid_direction(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        dws_column,
        ads_column,
    ) = create_four_level_lineage(
        db_session
    )

    with pytest.raises(
        ValueError,
        match="direction",
    ):
        get_column_lineage_graph(
            db=db_session,
            column_id=dwd_column.id,
            direction="left",
            max_depth=10,
        )


# ============================================================
# 7. 非法最大深度
# ============================================================

def test_reject_invalid_max_depth(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        dws_column,
        ads_column,
    ) = create_four_level_lineage(
        db_session
    )

    with pytest.raises(
        ValueError,
        match="max_depth",
    ):
        get_column_lineage_graph(
            db=db_session,
            column_id=dwd_column.id,
            direction="both",
            max_depth=0,
        )

    with pytest.raises(
        ValueError,
        match="max_depth",
    ):
        get_column_lineage_graph(
            db=db_session,
            column_id=dwd_column.id,
            direction="both",
            max_depth=21,
        )
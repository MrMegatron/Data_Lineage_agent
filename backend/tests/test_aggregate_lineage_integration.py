from uuid import uuid4

from sqlalchemy import (
    func,
    select,
)

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
)
from app.services.column_lineage_query_service import (
    get_column_lineage_detail,
)
from app.services.column_search_service import (
    search_project_columns,
)
from app.services.sql_file_scanner import (
    scan_sql_files,
)
from app.services.sql_ingestion_service import (
    ingest_scanned_sql_file,
)


# ============================================================
# 1. 创建测试项目
# ============================================================

def create_test_project(
    db_session,
) -> LineageProject:
    project = LineageProject(
        name=(
            "aggregate_integration_"
            f"{uuid4().hex[:8]}"
        ),
        description=(
            "聚合字段正式导入集成测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建聚合SQL文件
# ============================================================

def create_aggregate_sql_file(
    tmp_path,
):
    sql_root = (
        tmp_path
        / "sql_source"
    )

    hive_directory = (
        sql_root
        / "hive"
    )

    hive_directory.mkdir(
        parents=True
    )

    sql_file = (
        hive_directory
        / "aggregate_order_summary.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO ads.order_summary (\n"
            "    total_amount,\n"
            "    order_count\n"
            ")\n"
            "SELECT\n"
            "    SUM(amount) AS total_amount,\n"
            "    COUNT(order_id) AS order_count\n"
            "FROM dwd.orders;\n"
        ),
        encoding="utf-8",
    )

    return (
        sql_root,
        sql_file,
    )


# ============================================================
# 3. 聚合字段正式导入
# ============================================================

def test_formal_ingestion_creates_aggregate_lineage(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_aggregate_sql_file(
        tmp_path
    )

    # --------------------------------------------------------
    # 第一步：扫描SQL文件
    # --------------------------------------------------------

    scanned_files = scan_sql_files(
        sql_root
    )

    assert len(scanned_files) == 1

    scanned_file = scanned_files[0]

    # --------------------------------------------------------
    # 第二步：调用正式导入服务
    # --------------------------------------------------------

    ingestion_result = (
        ingest_scanned_sql_file(
            db=db_session,
            project_id=project.id,
            scanned_file=scanned_file,
        )
    )

    assert (
        ingestion_result.parse_status
        == "success"
    )

    script_id = (
        ingestion_result.script_id
    )

    # --------------------------------------------------------
    # 第三步：检查数据表
    # --------------------------------------------------------

    tables = list(
        db_session.scalars(
            select(DataTable)
            .where(
                DataTable.project_id
                == project.id
            )
        ).all()
    )

    assert len(tables) == 2

    assert {
        table.full_name
        for table in tables
    } == {
        "dwd.orders",
        "ads.order_summary",
    }

    # --------------------------------------------------------
    # 第四步：检查字段
    #
    # 来源字段：
    #
    #     dwd.orders.amount
    #     dwd.orders.order_id
    #
    # 目标字段：
    #
    #     ads.order_summary.total_amount
    #     ads.order_summary.order_count
    # --------------------------------------------------------

    columns = list(
        db_session.scalars(
            select(DataColumn)
            .join(
                DataTable,
                DataTable.id
                == DataColumn.table_id,
            )
            .where(
                DataTable.project_id
                == project.id
            )
        ).all()
    )

    assert len(columns) == 4

    actual_columns = {
        (
            column.table.full_name,
            column.column_name,
        )
        for column in columns
    }

    assert actual_columns == {
        (
            "dwd.orders",
            "amount",
        ),
        (
            "dwd.orders",
            "order_id",
        ),
        (
            "ads.order_summary",
            "total_amount",
        ),
        (
            "ads.order_summary",
            "order_count",
        ),
    }

    # --------------------------------------------------------
    # 第五步：检查聚合字段血缘
    # --------------------------------------------------------

    lineages = list(
        db_session.scalars(
            select(ColumnLineage)
            .where(
                ColumnLineage.script_id
                == script_id
            )
            .order_by(
                ColumnLineage.id
            )
        ).all()
    )

    assert len(lineages) == 2

    assert all(
        lineage.relation_type
        == "aggregate"
        for lineage in lineages
    )

    assert all(
        lineage.resolution_status
        == "confirmed"
        for lineage in lineages
    )

    actual_mappings = {
        (
            lineage
            .source_column
            .column_name,

            lineage
            .target_column
            .column_name,
        )
        for lineage in lineages
    }

    assert actual_mappings == {
        (
            "amount",
            "total_amount",
        ),
        (
            "order_id",
            "order_count",
        ),
    }

    expressions_by_target = {
        (
            lineage
            .target_column
            .column_name
        ): lineage.expression_text
        for lineage in lineages
    }

    assert (
        expressions_by_target[
            "total_amount"
        ]
        == "SUM(amount) AS total_amount"
    )

    assert (
        expressions_by_target[
            "order_count"
        ]
        == "COUNT(order_id) AS order_count"
    )

    # --------------------------------------------------------
    # 第六步：检查血缘证据
    # --------------------------------------------------------

    evidences = list(
        db_session.scalars(
            select(LineageEvidence)
            .where(
                LineageEvidence.script_id
                == script_id
            )
        ).all()
    )

    assert len(evidences) == 2

    evidence_snippets = {
        evidence.code_snippet
        for evidence in evidences
    }

    assert evidence_snippets == {
        "SUM(amount) AS total_amount",
        "COUNT(order_id) AS order_count",
    }

    # --------------------------------------------------------
    # 第七步：查询total_amount字段
    # --------------------------------------------------------

    total_amount_column = (
        db_session.scalar(
            select(DataColumn)
            .join(
                DataTable,
                DataTable.id
                == DataColumn.table_id,
            )
            .where(
                DataTable.project_id
                == project.id,
                DataTable.full_name
                == "ads.order_summary",
                DataColumn.column_name
                == "total_amount",
            )
        )
    )

    assert total_amount_column is not None

    lineage_detail = (
        get_column_lineage_detail(
            db=db_session,
            column_id=(
                total_amount_column.id
            ),
        )
    )

    assert (
        lineage_detail.upstream_count
        == 1
    )

    assert (
        lineage_detail
        .upstream_edges[0]
        .source_column
        .column_name
        == "amount"
    )

    assert (
        lineage_detail
        .upstream_edges[0]
        .relation_type
        == "aggregate"
    )

    # --------------------------------------------------------
    # 第八步：通过搜索服务查找字段
    # --------------------------------------------------------

    search_result = (
        search_project_columns(
            db=db_session,
            project_id=project.id,
            table_name=(
                "ads.order_summary"
            ),
            column_name=(
                "total_amount"
            ),
        )
    )

    assert search_result.total == 1

    assert (
        search_result.items[0]
        .column_id
        == total_amount_column.id
    )

    assert (
        search_result.items[0]
        .upstream_lineage_count
        == 1
    )


# ============================================================
# 4. 聚合字段重新导入不重复
# ============================================================

def test_aggregate_reingestion_is_idempotent(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_aggregate_sql_file(
        tmp_path
    )

    scanned_file = scan_sql_files(
        sql_root
    )[0]

    first_result = (
        ingest_scanned_sql_file(
            db=db_session,
            project_id=project.id,
            scanned_file=scanned_file,
        )
    )

    second_result = (
        ingest_scanned_sql_file(
            db=db_session,
            project_id=project.id,
            scanned_file=scanned_file,
        )
    )

    assert (
        first_result.script_id
        == second_result.script_id
    )

    column_count = int(
        db_session.scalar(
            select(
                func.count(
                    DataColumn.id
                )
            )
            .select_from(DataColumn)
            .join(
                DataTable,
                DataTable.id
                == DataColumn.table_id,
            )
            .where(
                DataTable.project_id
                == project.id
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
                == first_result.script_id
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
                == first_result.script_id
            )
        )
        or 0
    )

    assert column_count == 4
    assert lineage_count == 2
    assert evidence_count == 2
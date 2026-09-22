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
            "case_when_integration_"
            f"{uuid4().hex[:8]}"
        ),
        description=(
            "CASE WHEN字段血缘集成测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建CASE WHEN SQL文件
# ============================================================

def create_case_when_sql_file(
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
        / "build_order_status.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.orders (\n"
            "    status_name,\n"
            "    risk_level\n"
            ")\n"
            "SELECT\n"
            "    CASE\n"
            "        WHEN status = 1\n"
            "        THEN 'paid'\n"
            "        ELSE 'unpaid'\n"
            "    END AS status_name,\n"
            "    CASE\n"
            "        WHEN amount >= 1000\n"
            "             AND customer_level = 'vip'\n"
            "        THEN 'high'\n"
            "        ELSE 'normal'\n"
            "    END AS risk_level\n"
            "FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    return (
        sql_root,
        sql_file,
    )


# ============================================================
# 3. 正式导入CASE WHEN字段
# ============================================================

def test_formal_ingestion_creates_case_lineage(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_case_when_sql_file(
        tmp_path
    )

    scanned_files = scan_sql_files(
        sql_root
    )

    assert len(scanned_files) == 1

    ingestion_result = (
        ingest_scanned_sql_file(
            db=db_session,
            project_id=project.id,
            scanned_file=scanned_files[0],
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
    # 检查字段
    #
    # 来源字段：
    #
    # status
    # amount
    # customer_level
    #
    # 目标字段：
    #
    # status_name
    # risk_level
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

    assert len(columns) == 5

    actual_columns = {
        (
            column.table.full_name,
            column.column_name,
        )
        for column in columns
    }

    assert actual_columns == {
        (
            "ods.orders",
            "status",
        ),
        (
            "ods.orders",
            "amount",
        ),
        (
            "ods.orders",
            "customer_level",
        ),
        (
            "dwd.orders",
            "status_name",
        ),
        (
            "dwd.orders",
            "risk_level",
        ),
    }

    # --------------------------------------------------------
    # 检查字段血缘
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

    assert len(lineages) == 3

    assert all(
        lineage.relation_type
        == "transform"
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
            "status",
            "status_name",
        ),
        (
            "amount",
            "risk_level",
        ),
        (
            "customer_level",
            "risk_level",
        ),
    }

    # --------------------------------------------------------
    # 检查status_name
    # --------------------------------------------------------

    status_name_lineage = next(
        lineage
        for lineage in lineages
        if (
            lineage
            .target_column
            .column_name
            == "status_name"
        )
    )

    assert (
        "CASE"
        in status_name_lineage
        .expression_text
    )

    assert (
        "status"
        in status_name_lineage
        .expression_text
    )

    # --------------------------------------------------------
    # 检查risk_level两个来源
    # --------------------------------------------------------

    risk_lineages = [
        lineage
        for lineage in lineages
        if (
            lineage
            .target_column
            .column_name
            == "risk_level"
        )
    ]

    assert len(risk_lineages) == 2

    risk_sources = {
        lineage.source_column.column_name
        for lineage in risk_lineages
    }

    assert risk_sources == {
        "amount",
        "customer_level",
    }

    target_ids = {
        lineage.target_column_id
        for lineage in risk_lineages
    }

    # 两个来源必须指向同一个risk_level字段。
    assert len(target_ids) == 1

    risk_level_column_id = next(
        iter(target_ids)
    )

    # --------------------------------------------------------
    # 检查血缘证据
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

    assert len(evidences) == 3

    assert all(
        "CASE" in evidence.code_snippet
        for evidence in evidences
    )

    # --------------------------------------------------------
    # 使用字段详情查询risk_level
    # --------------------------------------------------------

    detail_result = (
        get_column_lineage_detail(
            db=db_session,
            column_id=(
                risk_level_column_id
            ),
        )
    )

    assert (
        detail_result
        .column
        .column_name
        == "risk_level"
    )

    assert (
        detail_result.upstream_count
        == 2
    )

    upstream_names = {
        edge.source_column.column_name
        for edge in (
            detail_result.upstream_edges
        )
    }

    assert upstream_names == {
        "amount",
        "customer_level",
    }

    assert all(
        edge.relation_type
        == "transform"
        for edge in (
            detail_result.upstream_edges
        )
    )

    # --------------------------------------------------------
    # 使用字段搜索查询risk_level
    # --------------------------------------------------------

    search_result = (
        search_project_columns(
            db=db_session,
            project_id=project.id,
            table_name="dwd.orders",
            column_name="risk_level",
        )
    )

    assert search_result.total == 1

    assert (
        search_result.items[0]
        .column_id
        == risk_level_column_id
    )

    assert (
        search_result.items[0]
        .upstream_lineage_count
        == 2
    )


# ============================================================
# 4. 重新导入不产生重复CASE血缘
# ============================================================

def test_case_when_reingestion_is_idempotent(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_case_when_sql_file(
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

    assert column_count == 5
    assert lineage_count == 3
    assert evidence_count == 3
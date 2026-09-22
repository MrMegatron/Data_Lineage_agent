from uuid import uuid4

from sqlalchemy import select

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
            "function_lineage_"
            f"{uuid4().hex[:8]}"
        ),
        description=(
            "普通函数字段血缘集成测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建函数转换SQL文件
# ============================================================

def create_function_sql_file(
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
        / "build_customer_detail.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.customer (\n"
            "    full_name,\n"
            "    phone_clean,\n"
            "    created_date\n"
            ")\n"
            "SELECT\n"
            "    CONCAT(\n"
            "        first_name,\n"
            "        ' ',\n"
            "        last_name\n"
            "    ) AS full_name,\n"
            "    COALESCE(\n"
            "        phone,\n"
            "        'unknown'\n"
            "    ) AS phone_clean,\n"
            "    DATE_FORMAT(\n"
            "        created_at,\n"
            "        'yyyy-MM-dd'\n"
            "    ) AS created_date\n"
            "FROM ods.customer;\n"
        ),
        encoding="utf-8",
    )

    return (
        sql_root,
        sql_file,
    )


# ============================================================
# 3. 正式导入函数字段
# ============================================================

def test_formal_ingestion_creates_function_lineage(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_function_sql_file(
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
    # 来源：
    #
    # first_name
    # last_name
    # phone
    # created_at
    #
    # 目标：
    #
    # full_name
    # phone_clean
    # created_date
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

    assert len(columns) == 7

    actual_columns = {
        (
            column.table.full_name,
            column.column_name,
        )
        for column in columns
    }

    assert actual_columns == {
        (
            "ods.customer",
            "first_name",
        ),
        (
            "ods.customer",
            "last_name",
        ),
        (
            "ods.customer",
            "phone",
        ),
        (
            "ods.customer",
            "created_at",
        ),
        (
            "dwd.customer",
            "full_name",
        ),
        (
            "dwd.customer",
            "phone_clean",
        ),
        (
            "dwd.customer",
            "created_date",
        ),
    }

    # --------------------------------------------------------
    # 检查四条血缘
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

    assert len(lineages) == 4

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
            "first_name",
            "full_name",
        ),
        (
            "last_name",
            "full_name",
        ),
        (
            "phone",
            "phone_clean",
        ),
        (
            "created_at",
            "created_date",
        ),
    }

    # --------------------------------------------------------
    # 检查表达式分类
    # --------------------------------------------------------

    full_name_lineages = [
        lineage
        for lineage in lineages
        if (
            lineage
            .target_column
            .column_name
            == "full_name"
        )
    ]

    assert len(full_name_lineages) == 2

    assert all(
        "CONCAT" in lineage.expression_text
        for lineage in full_name_lineages
    )

    phone_lineage = next(
        lineage
        for lineage in lineages
        if (
            lineage
            .target_column
            .column_name
            == "phone_clean"
        )
    )

    assert (
        "COALESCE"
        in phone_lineage.expression_text
    )

    date_lineage = next(
        lineage
        for lineage in lineages
        if (
            lineage
            .target_column
            .column_name
            == "created_date"
        )
    )

    assert (
        "DATE_FORMAT"
        in date_lineage.expression_text
    )

    # --------------------------------------------------------
    # 检查证据
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

    assert len(evidences) == 4

    # --------------------------------------------------------
    # 查询full_name字段
    # --------------------------------------------------------

    full_name_column = (
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
                == "dwd.customer",
                DataColumn.column_name
                == "full_name",
            )
        )
    )

    assert full_name_column is not None

    detail_result = (
        get_column_lineage_detail(
            db=db_session,
            column_id=(
                full_name_column.id
            ),
        )
    )

    # first_name和last_name
    assert detail_result.upstream_count == 2

    upstream_names = {
        edge.source_column.column_name
        for edge in (
            detail_result.upstream_edges
        )
    }

    assert upstream_names == {
        "first_name",
        "last_name",
    }

    # --------------------------------------------------------
    # 使用搜索服务查找full_name
    # --------------------------------------------------------

    search_result = (
        search_project_columns(
            db=db_session,
            project_id=project.id,
            table_name="dwd.customer",
            column_name="full_name",
        )
    )

    assert search_result.total == 1

    assert (
        search_result.items[0]
        .upstream_lineage_count
        == 2
    )
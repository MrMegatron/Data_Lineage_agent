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
    ScriptTableAccess,
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
            "cte_lineage_integration_"
            f"{uuid4().hex[:8]}"
        ),
        description=(
            "单层CTE字段血缘正式导入测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建CTE SQL文件
# ============================================================

def create_cte_sql_file(
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
        / "build_order_summary.sql"
    )

    sql_file.write_text(
        (
            "WITH order_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.orders\n"
            ")\n"
            "INSERT INTO dwd.order_summary (\n"
            "    order_id,\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    amount\n"
            "FROM order_base;\n"
        ),
        encoding="utf-8",
    )

    return (
        sql_root,
        sql_file,
    )


# ============================================================
# 3. 项目字段数量
# ============================================================

def count_project_columns(
    db_session,
    project_id: int,
) -> int:
    return int(
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
                == project_id
            )
        )
        or 0
    )


# ============================================================
# 4. 脚本字段血缘数量
# ============================================================

def count_script_lineages(
    db_session,
    script_id: int,
) -> int:
    return int(
        db_session.scalar(
            select(
                func.count(
                    ColumnLineage.id
                )
            )
            .where(
                ColumnLineage.script_id
                == script_id
            )
        )
        or 0
    )


# ============================================================
# 5. 脚本证据数量
# ============================================================

def count_script_evidences(
    db_session,
    script_id: int,
) -> int:
    return int(
        db_session.scalar(
            select(
                func.count(
                    LineageEvidence.id
                )
            )
            .where(
                LineageEvidence.script_id
                == script_id
            )
        )
        or 0
    )


# ============================================================
# 6. 正式导入CTE SQL
# ============================================================

def test_formal_ingestion_creates_cte_lineage(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_cte_sql_file(
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

    assert (
        scanned_file
        .relative_path
        .replace("\\", "/")
        == "hive/build_order_summary.sql"
    )

    # --------------------------------------------------------
    # 第二步：正式导入
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
    # 第三步：检查物理数据表
    #
    # 只能有：
    #
    # ods.orders
    # dwd.order_summary
    #
    # 不能有：
    #
    # order_base
    # --------------------------------------------------------

    tables = list(
        db_session.scalars(
            select(DataTable)
            .where(
                DataTable.project_id
                == project.id
            )
            .order_by(
                DataTable.full_name
            )
        ).all()
    )

    assert len(tables) == 2

    table_names = {
        table.full_name
        for table in tables
    }

    assert table_names == {
        "ods.orders",
        "dwd.order_summary",
    }

    assert (
        "order_base"
        not in table_names
    )

    # --------------------------------------------------------
    # 第四步：检查表级访问
    # --------------------------------------------------------

    table_accesses = list(
        db_session.scalars(
            select(ScriptTableAccess)
            .where(
                ScriptTableAccess.script_id
                == script_id
            )
        ).all()
    )

    assert len(table_accesses) == 2

    read_tables = {
        access.table.full_name
        for access in table_accesses
        if access.access_type == "read"
    }

    write_tables = {
        access.table.full_name
        for access in table_accesses
        if access.access_type == "write"
    }

    assert read_tables == {
        "ods.orders",
    }

    assert write_tables == {
        "dwd.order_summary",
    }

    # --------------------------------------------------------
    # 第五步：检查字段
    #
    # 来源：
    #
    # ods.orders.order_id
    # ods.orders.price
    # ods.orders.quantity
    #
    # 目标：
    #
    # dwd.order_summary.order_id
    # dwd.order_summary.amount
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
            "order_id",
        ),
        (
            "ods.orders",
            "price",
        ),
        (
            "ods.orders",
            "quantity",
        ),
        (
            "dwd.order_summary",
            "order_id",
        ),
        (
            "dwd.order_summary",
            "amount",
        ),
    }

    # --------------------------------------------------------
    # 第六步：检查字段血缘
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

    # order_id -> order_id
    # price    -> amount
    # quantity -> amount
    assert len(lineages) == 3

    actual_mappings = {
        (
            lineage
            .source_column
            .table
            .full_name,

            lineage
            .source_column
            .column_name,

            lineage
            .target_column
            .table
            .full_name,

            lineage
            .target_column
            .column_name,

            lineage.relation_type,
        )
        for lineage in lineages
    }

    assert actual_mappings == {
        (
            "ods.orders",
            "order_id",
            "dwd.order_summary",
            "order_id",
            "direct",
        ),
        (
            "ods.orders",
            "price",
            "dwd.order_summary",
            "amount",
            "transform",
        ),
        (
            "ods.orders",
            "quantity",
            "dwd.order_summary",
            "amount",
            "transform",
        ),
    }

    # --------------------------------------------------------
    # 第七步：检查amount表达式
    # --------------------------------------------------------

    amount_lineages = [
        lineage
        for lineage in lineages
        if (
            lineage
            .target_column
            .column_name
            == "amount"
        )
    ]

    assert len(amount_lineages) == 2

    assert all(
        (
            "price * quantity"
            in lineage.expression_text
        )
        for lineage in amount_lineages
    )

    amount_target_ids = {
        lineage.target_column_id
        for lineage in amount_lineages
    }

    assert len(amount_target_ids) == 1

    amount_column_id = next(
        iter(amount_target_ids)
    )

    # --------------------------------------------------------
    # 第八步：检查血缘证据
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

    amount_evidences = [
        evidence
        for evidence in evidences
        if (
            "price * quantity"
            in evidence.code_snippet
        )
    ]

    assert len(amount_evidences) == 2

    # --------------------------------------------------------
    # 第九步：查询amount直接血缘
    # --------------------------------------------------------

    detail_result = (
        get_column_lineage_detail(
            db=db_session,
            column_id=amount_column_id,
        )
    )

    assert (
        detail_result
        .column
        .table_full_name
        == "dwd.order_summary"
    )

    assert (
        detail_result
        .column
        .column_name
        == "amount"
    )

    assert detail_result.upstream_count == 2

    upstream_names = {
        edge.source_column.column_name
        for edge in (
            detail_result.upstream_edges
        )
    }

    assert upstream_names == {
        "price",
        "quantity",
    }

    # --------------------------------------------------------
    # 第十步：字段搜索
    # --------------------------------------------------------

    search_result = (
        search_project_columns(
            db=db_session,
            project_id=project.id,
            table_name=(
                "dwd.order_summary"
            ),
            column_name="amount",
        )
    )

    assert search_result.total == 1

    assert (
        search_result.items[0]
        .column_id
        == amount_column_id
    )

    assert (
        search_result.items[0]
        .upstream_lineage_count
        == 2
    )


# ============================================================
# 7. 重新导入CTE不产生重复数据
# ============================================================

def test_cte_reingestion_is_idempotent(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_cte_sql_file(
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

    assert (
        count_project_columns(
            db_session=db_session,
            project_id=project.id,
        )
        == 5
    )

    assert (
        count_script_lineages(
            db_session=db_session,
            script_id=first_result.script_id,
        )
        == 3
    )

    assert (
        count_script_evidences(
            db_session=db_session,
            script_id=first_result.script_id,
        )
        == 3
    )
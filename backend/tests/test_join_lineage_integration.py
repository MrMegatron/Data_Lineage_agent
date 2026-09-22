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
            "join_lineage_integration_"
            f"{uuid4().hex[:8]}"
        ),
        description=(
            "JOIN字段血缘正式导入集成测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建JOIN SQL文件
# ============================================================

def create_join_sql_file(
    tmp_path,
):
    """
    创建：

        ods.orders AS o
        JOIN
        ods.customer AS c

    目标表：

        dwd.order_detail
    """

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
        / "build_order_detail.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.order_detail (\n"
            "    order_id,\n"
            "    customer_name,\n"
            "    discounted_amount\n"
            ")\n"
            "SELECT\n"
            "    o.order_id,\n"
            "    c.customer_name,\n"
            "    o.amount * c.discount_rate\n"
            "        AS discounted_amount\n"
            "FROM ods.orders AS o\n"
            "JOIN ods.customer AS c\n"
            "    ON o.customer_id = c.customer_id;\n"
        ),
        encoding="utf-8",
    )

    return (
        sql_root,
        sql_file,
    )


# ============================================================
# 3. 统计项目字段数量
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
# 4. 统计脚本字段血缘数量
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
# 5. 统计脚本证据数量
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
# 6. 正式导入JOIN SQL
# ============================================================

def test_formal_ingestion_creates_join_lineage(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_join_sql_file(
        tmp_path
    )

    # --------------------------------------------------------
    # 第一步：正式扫描SQL文件
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
        == "hive/build_order_detail.sql"
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
    # 第三步：检查表级血缘
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

    assert len(tables) == 3

    assert {
        table.full_name
        for table in tables
    } == {
        "ods.orders",
        "ods.customer",
        "dwd.order_detail",
    }

    table_accesses = list(
        db_session.scalars(
            select(ScriptTableAccess)
            .where(
                ScriptTableAccess.script_id
                == script_id
            )
        ).all()
    )

    # READ ods.orders
    # READ ods.customer
    # WRITE dwd.order_detail
    assert len(table_accesses) == 3

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
        "ods.customer",
    }

    assert write_tables == {
        "dwd.order_detail",
    }

    # --------------------------------------------------------
    # 第四步：检查字段
    #
    # ods.orders：
    #     order_id
    #     amount
    #
    # ods.customer：
    #     customer_name
    #     discount_rate
    #
    # dwd.order_detail：
    #     order_id
    #     customer_name
    #     discounted_amount
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
            "ods.orders",
            "order_id",
        ),
        (
            "ods.orders",
            "amount",
        ),
        (
            "ods.customer",
            "customer_name",
        ),
        (
            "ods.customer",
            "discount_rate",
        ),
        (
            "dwd.order_detail",
            "order_id",
        ),
        (
            "dwd.order_detail",
            "customer_name",
        ),
        (
            "dwd.order_detail",
            "discounted_amount",
        ),
    }

    # --------------------------------------------------------
    # 第五步：检查字段血缘
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
    # customer_name -> customer_name
    # amount -> discounted_amount
    # discount_rate -> discounted_amount
    assert len(lineages) == 4

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
            "dwd.order_detail",
            "order_id",
            "direct",
        ),
        (
            "ods.customer",
            "customer_name",
            "dwd.order_detail",
            "customer_name",
            "direct",
        ),
        (
            "ods.orders",
            "amount",
            "dwd.order_detail",
            "discounted_amount",
            "transform",
        ),
        (
            "ods.customer",
            "discount_rate",
            "dwd.order_detail",
            "discounted_amount",
            "transform",
        ),
    }

    assert all(
        lineage.resolution_status
        == "confirmed"
        for lineage in lineages
    )

    # --------------------------------------------------------
    # 第六步：检查跨表转换表达式
    # --------------------------------------------------------

    transform_lineages = [
        lineage
        for lineage in lineages
        if (
            lineage
            .target_column
            .column_name
            == "discounted_amount"
        )
    ]

    assert len(transform_lineages) == 2

    assert all(
        lineage.relation_type
        == "transform"
        for lineage in transform_lineages
    )

    assert all(
        (
            "o.amount"
            in lineage.expression_text
        )
        for lineage in transform_lineages
    )

    assert all(
        (
            "c.discount_rate"
            in lineage.expression_text
        )
        for lineage in transform_lineages
    )

    target_column_ids = {
        lineage.target_column_id
        for lineage in transform_lineages
    }

    # 两个来源字段必须指向同一个目标字段。
    assert len(target_column_ids) == 1

    discounted_amount_column_id = next(
        iter(target_column_ids)
    )

    # --------------------------------------------------------
    # 第七步：检查血缘证据
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

    transform_evidences = [
        evidence
        for evidence in evidences
        if (
            "discounted_amount"
            in evidence.code_snippet
        )
    ]

    # amount和discount_rate各有一条血缘证据。
    assert len(transform_evidences) == 2

    # --------------------------------------------------------
    # 第八步：通过字段详情查询目标字段
    # --------------------------------------------------------

    detail_result = (
        get_column_lineage_detail(
            db=db_session,
            column_id=(
                discounted_amount_column_id
            ),
        )
    )

    assert (
        detail_result
        .column
        .table_full_name
        == "dwd.order_detail"
    )

    assert (
        detail_result
        .column
        .column_name
        == "discounted_amount"
    )

    assert detail_result.upstream_count == 2

    upstream_fields = {
        (
            edge
            .source_column
            .table_full_name,

            edge
            .source_column
            .column_name,
        )
        for edge in (
            detail_result.upstream_edges
        )
    }

    assert upstream_fields == {
        (
            "ods.orders",
            "amount",
        ),
        (
            "ods.customer",
            "discount_rate",
        ),
    }

    # --------------------------------------------------------
    # 第九步：通过字段搜索查询
    # --------------------------------------------------------

    search_result = (
        search_project_columns(
            db=db_session,
            project_id=project.id,
            table_name="dwd.order_detail",
            column_name=(
                "discounted_amount"
            ),
        )
    )

    assert search_result.total == 1

    assert (
        search_result.items[0]
        .column_id
        == discounted_amount_column_id
    )

    assert (
        search_result.items[0]
        .upstream_lineage_count
        == 2
    )


# ============================================================
# 7. 重新导入JOIN SQL不产生重复数据
# ============================================================

def test_join_reingestion_is_idempotent(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_join_sql_file(
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

    column_count = (
        count_project_columns(
            db_session=db_session,
            project_id=project.id,
        )
    )

    lineage_count = (
        count_script_lineages(
            db_session=db_session,
            script_id=first_result.script_id,
        )
    )

    evidence_count = (
        count_script_evidences(
            db_session=db_session,
            script_id=first_result.script_id,
        )
    )

    assert column_count == 7
    assert lineage_count == 4
    assert evidence_count == 4
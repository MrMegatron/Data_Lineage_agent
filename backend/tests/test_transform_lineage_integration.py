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
            "transform_integration_"
            f"{uuid4().hex[:8]}"
        ),
        description=(
            "转换字段正式导入集成测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建转换表达式SQL文件
# ============================================================

def create_transform_sql_file(
    tmp_path,
):
    """
    创建：

        price
          \
           → amount
          /
        quantity
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
        / "calculate_order_amount.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.orders (\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    price * quantity AS amount\n"
            "FROM ods.orders;\n"
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
# 6. 正式导入转换字段SQL
# ============================================================

def test_formal_ingestion_creates_transform_lineage(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_transform_sql_file(
        tmp_path
    )

    # --------------------------------------------------------
    # 第一步：通过正式扫描器扫描
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
        == (
            "hive/"
            "calculate_order_amount.sql"
        )
    )

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
            .order_by(
                DataTable.full_name
            )
        ).all()
    )

    assert len(tables) == 2

    assert {
        table.full_name
        for table in tables
    } == {
        "ods.orders",
        "dwd.orders",
    }

    # --------------------------------------------------------
    # 第四步：检查字段
    #
    # ods.orders.price
    # ods.orders.quantity
    # dwd.orders.amount
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
            .order_by(
                DataTable.full_name,
                DataColumn.column_name,
            )
        ).all()
    )

    assert len(columns) == 3

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
            "price",
        ),
        (
            "ods.orders",
            "quantity",
        ),
        (
            "dwd.orders",
            "amount",
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

    # price -> amount
    # quantity -> amount
    assert len(lineages) == 2

    source_column_names = {
        lineage.source_column.column_name
        for lineage in lineages
    }

    assert source_column_names == {
        "price",
        "quantity",
    }

    assert all(
        lineage
        .source_column
        .table
        .full_name
        == "ods.orders"
        for lineage in lineages
    )

    assert all(
        lineage
        .target_column
        .table
        .full_name
        == "dwd.orders"
        for lineage in lineages
    )

    assert all(
        lineage
        .target_column
        .column_name
        == "amount"
        for lineage in lineages
    )

    # 两条来源血缘必须指向同一个目标字段。
    target_column_ids = {
        lineage.target_column_id
        for lineage in lineages
    }

    assert len(target_column_ids) == 1

    target_column_id = next(
        iter(target_column_ids)
    )

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

    assert all(
        lineage.expression_text
        == (
            "price * quantity "
            "AS amount"
        )
        for lineage in lineages
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
            .order_by(
                LineageEvidence.id
            )
        ).all()
    )

    assert len(evidences) == 2

    assert all(
        evidence.code_snippet
        == (
            "price * quantity "
            "AS amount"
        )
        for evidence in evidences
    )

    assert all(
        evidence.expression_text
        == (
            "price * quantity "
            "AS amount"
        )
        for evidence in evidences
    )

    # --------------------------------------------------------
    # 第七步：使用正式字段详情服务查询目标字段
    # --------------------------------------------------------

    lineage_detail = (
        get_column_lineage_detail(
            db=db_session,
            column_id=target_column_id,
        )
    )

    assert (
        lineage_detail
        .column
        .table_full_name
        == "dwd.orders"
    )

    assert (
        lineage_detail
        .column
        .column_name
        == "amount"
    )

    # amount 有两个直接上游字段。
    assert (
        lineage_detail.upstream_count
        == 2
    )

    assert (
        lineage_detail.downstream_count
        == 0
    )

    upstream_source_names = {
        edge.source_column.column_name
        for edge in (
            lineage_detail.upstream_edges
        )
    }

    assert upstream_source_names == {
        "price",
        "quantity",
    }

    assert all(
        edge.relation_type
        == "transform"
        for edge in (
            lineage_detail.upstream_edges
        )
    )

    # --------------------------------------------------------
    # 第八步：使用正式字段搜索服务
    # --------------------------------------------------------

    search_result = (
        search_project_columns(
            db=db_session,
            project_id=project.id,
            table_name="dwd.orders",
            column_name="amount",
        )
    )

    assert search_result.total == 1

    search_item = (
        search_result.items[0]
    )

    assert (
        search_item.column_id
        == target_column_id
    )

    assert (
        search_item
        .upstream_lineage_count
        == 2
    )

    assert (
        search_item
        .downstream_lineage_count
        == 0
    )


# ============================================================
# 7. 重新导入不产生重复转换血缘
# ============================================================

def test_transform_reingestion_is_idempotent(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_transform_sql_file(
        tmp_path
    )

    scanned_file = scan_sql_files(
        sql_root
    )[0]

    # --------------------------------------------------------
    # 第一次导入
    # --------------------------------------------------------

    first_result = (
        ingest_scanned_sql_file(
            db=db_session,
            project_id=project.id,
            scanned_file=scanned_file,
        )
    )

    assert (
        count_project_columns(
            db_session=db_session,
            project_id=project.id,
        )
        == 3
    )

    assert (
        count_script_lineages(
            db_session=db_session,
            script_id=first_result.script_id,
        )
        == 2
    )

    assert (
        count_script_evidences(
            db_session=db_session,
            script_id=first_result.script_id,
        )
        == 2
    )

    # --------------------------------------------------------
    # 第二次导入同一文件
    # --------------------------------------------------------

    second_result = (
        ingest_scanned_sql_file(
            db=db_session,
            project_id=project.id,
            scanned_file=scanned_file,
        )
    )

    # 必须更新同一条 SourceScript。
    assert (
        second_result.script_id
        == first_result.script_id
    )

    # 字段仍然只有三个。
    assert (
        count_project_columns(
            db_session=db_session,
            project_id=project.id,
        )
        == 3
    )

    # 血缘仍然只有两条。
    assert (
        count_script_lineages(
            db_session=db_session,
            script_id=first_result.script_id,
        )
        == 2
    )

    # 证据仍然只有两条。
    assert (
        count_script_evidences(
            db_session=db_session,
            script_id=first_result.script_id,
        )
        == 2
    )
from uuid import uuid4

from sqlalchemy import func, select

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
)
from app.services.sql_file_scanner import (
    scan_sql_files,
)
from app.services.sql_ingestion_service import (
    ingest_scanned_sql_file,
)


# ============================================================
# 1. 创建独立测试项目
# ============================================================

def create_test_project(
    db_session,
) -> LineageProject:
    """
    创建一个独立测试项目。

    使用随机后缀，避免不同测试之间的项目名称冲突。
    """

    project = LineageProject(
        name=(
            "sql_ingestion_column_test_"
            f"{uuid4().hex[:8]}"
        ),
        description=(
            "正式 SQL 导入自动生成字段血缘测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建测试 SQL 文件
# ============================================================

def create_test_sql_file(
    tmp_path,
):
    """
    创建一份带有显式目标字段的 SQL 文件。

    预期字段血缘：

        ods.orders.order_id
            -> dwd.orders.order_id

        ods.orders.customer_id
            -> dwd.orders.customer_id

        ods.orders.amount
            -> dwd.orders.amount
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
        / "01_ods_to_dwd.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.orders (\n"
            "    order_id,\n"
            "    customer_id,\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    customer_id,\n"
            "    amount\n"
            "FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    return (
        sql_root,
        sql_file,
    )


# ============================================================
# 3. 查询当前项目的字段数量
# ============================================================

def get_project_column_count(
    db_session,
    project_id: int,
) -> int:
    """
    统计指定项目所有数据表中的字段数量。
    """

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
# 4. 查询当前脚本的字段血缘数量
# ============================================================

def get_script_lineage_count(
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
# 5. 查询当前脚本的血缘证据数量
# ============================================================

def get_script_evidence_count(
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
# 6. 正式导入自动生成字段血缘
# ============================================================

def test_sql_ingestion_creates_column_lineage(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_test_sql_file(
        tmp_path
    )

    # --------------------------------------------------------
    # 使用正式扫描服务读取 SQL 文件
    # --------------------------------------------------------

    scanned_files = scan_sql_files(
        sql_root
    )

    assert len(scanned_files) == 1

    scanned_file = scanned_files[0]

    assert (
        scanned_file.relative_path
        == "hive/01_ods_to_dwd.sql"
    )

    # --------------------------------------------------------
    # 只调用正式单文件导入服务
    #
    # 测试中没有直接调用：
    #
    # extract_direct_column_lineage()
    # persist_direct_column_lineage()
    # process_script_column_lineage()
    #
    # 如果字段血缘能够生成，
    # 就能证明自动接入已经成功。
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
    # 检查 DataColumn
    # --------------------------------------------------------
    #
    # 来源表：
    #
    #     ods.orders
    #
    # 有三个字段。
    #
    # 目标表：
    #
    #     dwd.orders
    #
    # 也有三个字段。
    #
    # 所以一共应该有六条 DataColumn。
    # --------------------------------------------------------

    column_count = (
        get_project_column_count(
            db_session=db_session,
            project_id=project.id,
        )
    )

    assert column_count == 6

    # --------------------------------------------------------
    # 检查 ColumnLineage
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
        == "direct"
        for lineage in lineages
    )

    assert all(
        lineage.resolution_status
        == "confirmed"
        for lineage in lineages
    )

    assert all(
        lineage.statement_no == 1
        for lineage in lineages
    )

    # --------------------------------------------------------
    # 使用 ORM relationship 检查实际字段对应关系
    # --------------------------------------------------------

    actual_mappings = {
        (
            lineage.source_column.table.full_name,
            lineage.source_column.column_name,
            lineage.target_column.table.full_name,
            lineage.target_column.column_name,
        )
        for lineage in lineages
    }

    expected_mappings = {
        (
            "ods.orders",
            "order_id",
            "dwd.orders",
            "order_id",
        ),
        (
            "ods.orders",
            "customer_id",
            "dwd.orders",
            "customer_id",
        ),
        (
            "ods.orders",
            "amount",
            "dwd.orders",
            "amount",
        ),
    }

    assert (
        actual_mappings
        == expected_mappings
    )

    # --------------------------------------------------------
    # 检查 LineageEvidence
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

    evidence_snippets = {
        evidence.code_snippet
        for evidence in evidences
    }

    assert evidence_snippets == {
        "order_id",
        "customer_id",
        "amount",
    }


# ============================================================
# 7. 重新导入不会生成重复字段血缘
# ============================================================

def test_reingestion_does_not_duplicate_column_lineage(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session
    )

    (
        sql_root,
        sql_file,
    ) = create_test_sql_file(
        tmp_path
    )

    scanned_files = scan_sql_files(
        sql_root
    )

    assert len(scanned_files) == 1

    scanned_file = scanned_files[0]

    # --------------------------------------------------------
    # 第一次正式导入
    # --------------------------------------------------------

    first_result = ingest_scanned_sql_file(
        db=db_session,
        project_id=project.id,
        scanned_file=scanned_file,
    )

    first_script_id = (
        first_result.script_id
    )

    assert (
        get_project_column_count(
            db_session=db_session,
            project_id=project.id,
        )
        == 6
    )

    assert (
        get_script_lineage_count(
            db_session=db_session,
            script_id=first_script_id,
        )
        == 3
    )

    assert (
        get_script_evidence_count(
            db_session=db_session,
            script_id=first_script_id,
        )
        == 3
    )

    # --------------------------------------------------------
    # 第二次导入同一个文件
    # --------------------------------------------------------

    second_result = ingest_scanned_sql_file(
        db=db_session,
        project_id=project.id,
        scanned_file=scanned_file,
    )

    second_script_id = (
        second_result.script_id
    )

    # 应该更新原脚本，而不是创建新脚本。
    assert (
        second_script_id
        == first_script_id
    )

    # 字段仍然只有六个。
    assert (
        get_project_column_count(
            db_session=db_session,
            project_id=project.id,
        )
        == 6
    )

    # 血缘仍然只有三条。
    assert (
        get_script_lineage_count(
            db_session=db_session,
            script_id=first_script_id,
        )
        == 3
    )

    # 证据仍然只有三条。
    assert (
        get_script_evidence_count(
            db_session=db_session,
            script_id=first_script_id,
        )
        == 3
    )
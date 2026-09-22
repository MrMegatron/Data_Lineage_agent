from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
    ScriptTableAccess,
    SourceScript,
)
from app.services.sql_directory_ingestion_service import (
    ingest_sql_directory,
)


def create_test_project(
    db_session,
) -> LineageProject:
    """
    创建一个名称唯一的测试项目。

    使用 uuid 是为了避免多次运行测试时，
    项目名称与历史测试数据冲突。
    """

    project = LineageProject(
        name=(
            "multiple_cte_integration_"
            f"{uuid4().hex[:12]}"
        ),
        description=(
            "多个并列 CTE 字段血缘集成测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    assert project.id is not None

    return project


def test_ingest_multiple_parallel_ctes(
    db_session,
    tmp_path,
):
    """
    完整测试两个并列 CTE 的正式导入。

    第一个 CTE：

        order_base
            ↓
        ods.orders

    第二个 CTE：

        customer_base
            ↓
        ods.customers

    最终目标表：

        dwd.order_detail

    预期字段血缘：

        ods.orders.order_id
            -> dwd.order_detail.order_id

        ods.customers.customer_name
            -> dwd.order_detail.customer_name

        ods.orders.price
            -> dwd.order_detail.amount

        ods.orders.quantity
            -> dwd.order_detail.amount
    """

    # ========================================================
    # 1. 创建测试项目
    # ========================================================

    project = create_test_project(
        db_session=db_session,
    )

    # ========================================================
    # 2. 创建临时 SQL 目录
    # ========================================================

    sql_root = tmp_path / "sql_source"

    hive_directory = (
        sql_root
        / "hive"
    )

    hive_directory.mkdir(
        parents=True,
    )

    sql_file = (
        hive_directory
        / "multiple_cte_order_detail.sql"
    )

    # ========================================================
    # 3. 写入包含两个并列 CTE 的 SQL
    # ========================================================

    sql_file.write_text(
        (
            "WITH order_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        customer_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.orders\n"
            "),\n"
            "customer_base AS (\n"
            "    SELECT\n"
            "        customer_id,\n"
            "        customer_name\n"
            "    FROM ods.customers\n"
            ")\n"
            "INSERT INTO dwd.order_detail (\n"
            "    order_id,\n"
            "    customer_name,\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    o.order_id,\n"
            "    c.customer_name,\n"
            "    o.amount\n"
            "FROM order_base AS o\n"
            "JOIN customer_base AS c\n"
            "  ON o.customer_id = c.customer_id;\n"
        ),
        encoding="utf-8",
    )

    assert sql_file.exists()
    assert sql_file.is_file()

    # ========================================================
    # 4. 第一次正式导入
    # ========================================================

    first_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    # ========================================================
    # 5. 检查目录导入汇总
    # ========================================================

    assert first_result.status == "success"

    assert first_result.total_files == 1
    assert first_result.success_files == 1
    assert first_result.failed_files == 0

    assert first_result.created_scripts == 1
    assert first_result.updated_scripts == 0

    assert first_result.total_statements == 1
    assert first_result.successful_statements == 1
    assert first_result.failed_statements == 0

    # 两张来源表 READ：
    #
    #     ods.orders
    #     ods.customers
    #
    # 一张目标表 WRITE：
    #
    #     dwd.order_detail
    assert first_result.table_access_count == 3

    # ========================================================
    # 6. 查询已经保存的 SourceScript
    # ========================================================

    script = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).one()

    assert script.file_name == (
        "multiple_cte_order_detail.sql"
    )

    assert script.relative_path == (
        "hive/multiple_cte_order_detail.sql"
    )

    assert script.dialect == "hive"
    assert script.parse_status == "success"
    assert script.parse_error is None

    # ========================================================
    # 7. 检查 DataTable
    #
    # CTE 名称不能保存成物理表：
    #
    #     order_base
    #     customer_base
    # ========================================================

    tables = db_session.scalars(
        select(DataTable)
        .where(
            DataTable.project_id
            == project.id
        )
        .order_by(
            DataTable.full_name
        )
    ).all()

    table_full_names = {
        table.full_name
        for table in tables
    }

    assert table_full_names == {
        "ods.orders",
        "ods.customers",
        "dwd.order_detail",
    }

    assert "order_base" not in table_full_names
    assert "customer_base" not in table_full_names

    assert len(tables) == 3

    table_by_full_name = {
        table.full_name: table
        for table in tables
    }

    source_order_table = table_by_full_name[
        "ods.orders"
    ]

    source_customer_table = table_by_full_name[
        "ods.customers"
    ]

    target_table = table_by_full_name[
        "dwd.order_detail"
    ]

    # ========================================================
    # 8. 检查表级 READ/WRITE
    # ========================================================

    access_rows = db_session.execute(
        select(
            DataTable.full_name,
            ScriptTableAccess.access_type,
        )
        .join(
            DataTable,
            DataTable.id
            == ScriptTableAccess.table_id,
        )
        .where(
            ScriptTableAccess.script_id
            == script.id
        )
    ).all()

    actual_accesses = {
        (
            full_name,
            access_type,
        )
        for (
            full_name,
            access_type,
        ) in access_rows
    }

    assert actual_accesses == {
        (
            "ods.orders",
            "read",
        ),
        (
            "ods.customers",
            "read",
        ),
        (
            "dwd.order_detail",
            "write",
        ),
    }

    # ========================================================
    # 9. 检查 DataColumn
    # ========================================================

    columns = db_session.scalars(
        select(DataColumn)
        .where(
            DataColumn.table_id.in_(
                [
                    source_order_table.id,
                    source_customer_table.id,
                    target_table.id,
                ]
            )
        )
        .order_by(
            DataColumn.table_id,
            DataColumn.column_name,
        )
    ).all()

    column_names_by_table_id: dict[
        int,
        set[str],
    ] = {}

    for column in columns:
        column_names_by_table_id.setdefault(
            column.table_id,
            set(),
        ).add(
            column.column_name
        )

    assert column_names_by_table_id[
               source_order_table.id
           ] == {
               "order_id",
               "price",
               "quantity",
           }

    assert column_names_by_table_id[
               source_customer_table.id
           ] == {
               "customer_name",
           }

    assert column_names_by_table_id[
        target_table.id
    ] == {
        "order_id",
        "customer_name",
        "amount",
    }
    # customer_id 只出现在 JOIN 条件中，
    # 不属于当前阶段的目标字段值血缘。
    assert "customer_id" not in (
        column_names_by_table_id[
            source_order_table.id
        ]
    )

    assert "customer_id" not in (
        column_names_by_table_id[
            source_customer_table.id
        ]
    )
    # 当前阶段保存的是“值字段血缘”涉及的字段：
    #
    # ods.orders:
    #     order_id
    #     price
    #     quantity
    #     共 3 个
    #
    # ods.customers:
    #     customer_name
    #     共 1 个
    #
    # dwd.order_detail:
    #     order_id
    #     customer_name
    #     amount
    #     共 3 个
    #
    # JOIN 条件中的 customer_id 只决定行匹配关系，
    # 当前阶段不把它作为目标字段的值来源。
    assert len(columns) == 7

    # ========================================================
    # 10. 查询字段血缘
    # ========================================================

    source_column = aliased(
        DataColumn
    )

    source_table_alias = aliased(
        DataTable
    )

    target_column = aliased(
        DataColumn
    )

    target_table_alias = aliased(
        DataTable
    )

    lineage_rows = db_session.execute(
        select(
            source_table_alias.full_name,
            source_column.column_name,
            target_table_alias.full_name,
            target_column.column_name,
            ColumnLineage.relation_type,
            ColumnLineage.resolution_status,
            ColumnLineage.expression_text,
        )
        .join(
            source_column,
            source_column.id
            == ColumnLineage.source_column_id,
        )
        .join(
            source_table_alias,
            source_table_alias.id
            == source_column.table_id,
        )
        .join(
            target_column,
            target_column.id
            == ColumnLineage.target_column_id,
        )
        .join(
            target_table_alias,
            target_table_alias.id
            == target_column.table_id,
        )
        .where(
            ColumnLineage.project_id
            == project.id,
            ColumnLineage.script_id
            == script.id,
        )
    ).all()

    # amount 有两个来源字段，
    # 所以总共应该是 4 条字段血缘。
    assert len(lineage_rows) == 4

    actual_lineages = {
        (
            source_table_full_name,
            source_column_name,
            target_table_full_name,
            target_column_name,
            relation_type,
            resolution_status,
            expression_text,
        )
        for (
            source_table_full_name,
            source_column_name,
            target_table_full_name,
            target_column_name,
            relation_type,
            resolution_status,
            expression_text,
        ) in lineage_rows
    }

    assert actual_lineages == {
        (
            "ods.orders",
            "order_id",
            "dwd.order_detail",
            "order_id",
            "direct",
            "confirmed",
            "order_id",
        ),
        (
            "ods.customers",
            "customer_name",
            "dwd.order_detail",
            "customer_name",
            "direct",
            "confirmed",
            "customer_name",
        ),
        (
            "ods.orders",
            "price",
            "dwd.order_detail",
            "amount",
            "transform",
            "confirmed",
            "price * quantity",
        ),
        (
            "ods.orders",
            "quantity",
            "dwd.order_detail",
            "amount",
            "transform",
            "confirmed",
            "price * quantity",
        ),
    }

    # ========================================================
    # 11. 检查 LineageEvidence
    # ========================================================

    evidences = db_session.scalars(
        select(LineageEvidence)
        .where(
            LineageEvidence.script_id
            == script.id
        )
        .order_by(
            LineageEvidence.id
        )
    ).all()

    assert len(evidences) == 4

    assert all(
        evidence.column_lineage_id
        is not None
        for evidence in evidences
    )

    assert all(
        evidence.statement_no == 1
        for evidence in evidences
    )

    # amount 的两条字段血缘都应该保留
    # price * quantity 的代码证据。
    amount_evidences = [
        evidence
        for evidence in evidences
        if evidence.expression_text
        == "price * quantity"
    ]

    assert len(amount_evidences) == 2

    # ========================================================
    # 12. 第二次导入同一个目录
    #
    # 验证重新导入不会重复创建：
    #
    # SourceScript
    # DataTable
    # DataColumn
    # ColumnLineage
    # LineageEvidence
    # ========================================================

    second_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert second_result.status == "success"

    assert second_result.total_files == 1
    assert second_result.success_files == 1
    assert second_result.failed_files == 0

    assert second_result.created_scripts == 0
    assert second_result.updated_scripts == 1

    # ========================================================
    # 13. 重新查询并检查数量没有翻倍
    # ========================================================

    scripts_after_reingest = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).all()

    assert len(scripts_after_reingest) == 1

    tables_after_reingest = db_session.scalars(
        select(DataTable).where(
            DataTable.project_id
            == project.id
        )
    ).all()

    assert len(tables_after_reingest) == 3

    columns_after_reingest = db_session.scalars(
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

    assert len(columns_after_reingest) == 7

    accesses_after_reingest = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id
            == script.id
        )
    ).all()

    assert len(accesses_after_reingest) == 3

    lineages_after_reingest = db_session.scalars(
        select(ColumnLineage).where(
            ColumnLineage.project_id
            == project.id,
            ColumnLineage.script_id
            == script.id,
        )
    ).all()

    assert len(lineages_after_reingest) == 4

    evidences_after_reingest = db_session.scalars(
        select(LineageEvidence).where(
            LineageEvidence.script_id
            == script.id
        )
    ).all()

    assert len(evidences_after_reingest) == 4

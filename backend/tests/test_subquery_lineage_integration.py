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


def create_subquery_test_project(
    db_session,
) -> LineageProject:
    """
    创建名称唯一的子查询测试项目。
    """

    project = LineageProject(
        name=(
            "subquery_integration_"
            f"{uuid4().hex[:12]}"
        ),
        description="子查询字段血缘集成测试",
    )

    db_session.add(project)
    db_session.flush()

    assert project.id is not None

    return project


def test_ingest_nested_subquery_lineage(
    db_session,
    tmp_path,
):
    """
    测试多层子查询的完整数据库导入。

    第一层：

        price * quantity AS amount

    第二层：

        直接传递 amount

    最外层：

        amount * 1.1 AS tax_amount

    最终物理来源：

        ods.orders.price
        ods.orders.quantity

    最终目标：

        dwd.order_tax.tax_amount
    """

    # ========================================================
    # 1. 创建测试项目
    # ========================================================

    project = create_subquery_test_project(
        db_session=db_session,
    )

    # ========================================================
    # 2. 创建临时 SQL 文件
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
        / "nested_subquery_order_tax.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.order_tax (\n"
            "    order_id,\n"
            "    tax_amount\n"
            ")\n"
            "SELECT\n"
            "    level_two.order_id,\n"
            "    level_two.amount * 1.1 AS tax_amount\n"
            "FROM (\n"
            "    SELECT\n"
            "        level_one.order_id,\n"
            "        level_one.amount\n"
            "    FROM (\n"
            "        SELECT\n"
            "            order_id,\n"
            "            price * quantity AS amount\n"
            "        FROM ods.orders\n"
            "    ) AS level_one\n"
            ") AS level_two;\n"
        ),
        encoding="utf-8",
    )

    assert sql_file.exists()
    assert sql_file.is_file()

    # ========================================================
    # 3. 第一次正式导入
    # ========================================================

    first_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    # ========================================================
    # 4. 检查导入汇总
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

    # READ  ods.orders
    # WRITE dwd.order_tax
    assert first_result.table_access_count == 2

    # ========================================================
    # 5. 检查 SourceScript
    # ========================================================

    script = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).one()

    assert script.file_name == (
        "nested_subquery_order_tax.sql"
    )

    assert script.relative_path == (
        "hive/nested_subquery_order_tax.sql"
    )

    assert script.dialect == "hive"
    assert script.parse_status == "success"
    assert script.parse_error is None

    # ========================================================
    # 6. 检查 DataTable
    #
    # 只能保存：
    #
    # ods.orders
    # dwd.order_tax
    #
    # 不能保存：
    #
    # level_one
    # level_two
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
        "dwd.order_tax",
    }

    assert len(tables) == 2

    assert "level_one" not in table_full_names
    assert "level_two" not in table_full_names

    table_by_full_name = {
        table.full_name: table
        for table in tables
    }

    source_table = table_by_full_name[
        "ods.orders"
    ]

    target_table = table_by_full_name[
        "dwd.order_tax"
    ]

    # ========================================================
    # 7. 检查 ScriptTableAccess
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
            "dwd.order_tax",
            "write",
        ),
    }

    assert len(access_rows) == 2

    # ========================================================
    # 8. 检查 DataColumn
    #
    # ods.orders：
    #
    #     order_id
    #     price
    #     quantity
    #
    # dwd.order_tax：
    #
    #     order_id
    #     tax_amount
    #
    # 共5个字段。
    # ========================================================

    columns = db_session.scalars(
        select(DataColumn)
        .where(
            DataColumn.table_id.in_(
                [
                    source_table.id,
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
        source_table.id
    ] == {
        "order_id",
        "price",
        "quantity",
    }

    assert column_names_by_table_id[
        target_table.id
    ] == {
        "order_id",
        "tax_amount",
    }

    assert len(columns) == 5

    # ========================================================
    # 9. 查询字段血缘
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

    # order_id 一条；
    # tax_amount 对应 price 和 quantity 两条。
    assert len(lineage_rows) == 3

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
            "dwd.order_tax",
            "order_id",
            "direct",
            "confirmed",
            "order_id",
        ),
        (
            "ods.orders",
            "price",
            "dwd.order_tax",
            "tax_amount",
            "transform",
            "confirmed",
            "level_two.amount * 1.1",
        ),
        (
            "ods.orders",
            "quantity",
            "dwd.order_tax",
            "tax_amount",
            "transform",
            "confirmed",
            "level_two.amount * 1.1",
        ),
    }

    # ========================================================
    # 10. 单独检查 tax_amount
    # ========================================================

    tax_amount_rows = [
        row
        for row in lineage_rows
        if row[3] == "tax_amount"
    ]

    assert len(tax_amount_rows) == 2

    assert {
        row[1]
        for row in tax_amount_rows
    } == {
        "price",
        "quantity",
    }

    assert all(
        row[0] == "ods.orders"
        for row in tax_amount_rows
    )

    assert all(
        row[2] == "dwd.order_tax"
        for row in tax_amount_rows
    )

    assert all(
        row[4] == "transform"
        for row in tax_amount_rows
    )

    assert all(
        row[5] == "confirmed"
        for row in tax_amount_rows
    )

    assert all(
        row[6]
        == "level_two.amount * 1.1"
        for row in tax_amount_rows
    )

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

    assert len(evidences) == 3

    assert all(
        evidence.column_lineage_id
        is not None
        for evidence in evidences
    )

    assert all(
        evidence.statement_no == 1
        for evidence in evidences
    )

    tax_amount_evidences = [
        evidence
        for evidence in evidences
        if evidence.expression_text
        == "level_two.amount * 1.1"
    ]

    assert len(tax_amount_evidences) == 2

    assert all(
        evidence.code_snippet
        for evidence in tax_amount_evidences
    )

    # ========================================================
    # 12. 第二次导入，验证幂等性
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
    # 13. 检查重新导入后的数量
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

    assert len(tables_after_reingest) == 2

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

    assert len(columns_after_reingest) == 5

    accesses_after_reingest = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id
            == script.id
        )
    ).all()

    assert len(accesses_after_reingest) == 2

    lineages_after_reingest = db_session.scalars(
        select(ColumnLineage).where(
            ColumnLineage.project_id
            == project.id,
            ColumnLineage.script_id
            == script.id,
        )
    ).all()

    assert len(lineages_after_reingest) == 3

    evidences_after_reingest = db_session.scalars(
        select(LineageEvidence).where(
            LineageEvidence.script_id
            == script.id
        )
    ).all()

    assert len(evidences_after_reingest) == 3
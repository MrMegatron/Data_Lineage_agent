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


def create_union_test_project(
    db_session,
) -> LineageProject:
    """
    创建名称唯一的 UNION ALL 测试项目。
    """

    project = LineageProject(
        name=(
            "union_all_integration_"
            f"{uuid4().hex[:12]}"
        ),
        description=(
            "UNION ALL 字段血缘集成测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    assert project.id is not None

    return project


def test_ingest_union_all_lineage(
    db_session,
    tmp_path,
):
    """
    测试 UNION ALL 的完整数据库导入。

    第一个分支：

        ods.web_orders.order_id
            -> dwd.all_orders.order_id

        ods.web_orders.price
        ods.web_orders.quantity
            -> dwd.all_orders.amount

    第二个分支：

        ods.store_orders.order_id
            -> dwd.all_orders.order_id

        ods.store_orders.amount
            -> dwd.all_orders.amount
    """

    # ========================================================
    # 1. 创建测试项目
    # ========================================================

    project = create_union_test_project(
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
        / "union_all_orders.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.all_orders (\n"
            "    order_id,\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    price * quantity AS amount\n"
            "FROM ods.web_orders\n"
            "\n"
            "UNION ALL\n"
            "\n"
            "SELECT\n"
            "    order_id,\n"
            "    amount\n"
            "FROM ods.store_orders;\n"
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

    # READ  ods.web_orders
    # READ  ods.store_orders
    # WRITE dwd.all_orders
    assert first_result.table_access_count == 3

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
        "union_all_orders.sql"
    )

    assert script.relative_path == (
        "hive/union_all_orders.sql"
    )

    assert script.dialect == "hive"
    assert script.parse_status == "success"
    assert script.parse_error is None

    # ========================================================
    # 6. 检查 DataTable
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
        "ods.web_orders",
        "ods.store_orders",
        "dwd.all_orders",
    }

    assert len(tables) == 3

    table_by_full_name = {
        table.full_name: table
        for table in tables
    }

    web_orders_table = table_by_full_name[
        "ods.web_orders"
    ]

    store_orders_table = table_by_full_name[
        "ods.store_orders"
    ]

    target_table = table_by_full_name[
        "dwd.all_orders"
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
            "ods.web_orders",
            "read",
        ),
        (
            "ods.store_orders",
            "read",
        ),
        (
            "dwd.all_orders",
            "write",
        ),
    }

    assert len(access_rows) == 3

    # ========================================================
    # 8. 检查 DataColumn
    #
    # ods.web_orders：
    #
    #     order_id
    #     price
    #     quantity
    #
    # ods.store_orders：
    #
    #     order_id
    #     amount
    #
    # dwd.all_orders：
    #
    #     order_id
    #     amount
    #
    # 共7个字段。
    # ========================================================

    columns = db_session.scalars(
        select(DataColumn)
        .where(
            DataColumn.table_id.in_(
                [
                    web_orders_table.id,
                    store_orders_table.id,
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
        web_orders_table.id
    ] == {
        "order_id",
        "price",
        "quantity",
    }

    assert column_names_by_table_id[
        store_orders_table.id
    ] == {
        "order_id",
        "amount",
    }

    assert column_names_by_table_id[
        target_table.id
    ] == {
        "order_id",
        "amount",
    }

    assert len(columns) == 7

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

    # web_orders：
    #
    #     order_id
    #     price
    #     quantity
    #
    # store_orders：
    #
    #     order_id
    #     amount
    #
    # 共5条字段血缘。
    assert len(lineage_rows) == 5

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
            "ods.web_orders",
            "order_id",
            "dwd.all_orders",
            "order_id",
            "direct",
            "confirmed",
            "order_id",
        ),
        (
            "ods.web_orders",
            "price",
            "dwd.all_orders",
            "amount",
            "transform",
            "confirmed",
            "price * quantity",
        ),
        (
            "ods.web_orders",
            "quantity",
            "dwd.all_orders",
            "amount",
            "transform",
            "confirmed",
            "price * quantity",
        ),
        (
            "ods.store_orders",
            "order_id",
            "dwd.all_orders",
            "order_id",
            "direct",
            "confirmed",
            "order_id",
        ),
        (
            "ods.store_orders",
            "amount",
            "dwd.all_orders",
            "amount",
            "direct",
            "confirmed",
            "amount",
        ),
    }

    # ========================================================
    # 10. 单独检查目标 order_id
    # ========================================================

    order_id_rows = [
        row
        for row in lineage_rows
        if row[3] == "order_id"
    ]

    assert len(order_id_rows) == 2

    assert {
        (
            row[0],
            row[1],
        )
        for row in order_id_rows
    } == {
        (
            "ods.web_orders",
            "order_id",
        ),
        (
            "ods.store_orders",
            "order_id",
        ),
    }

    assert all(
        row[4] == "direct"
        for row in order_id_rows
    )

    # ========================================================
    # 11. 单独检查目标 amount
    # ========================================================

    amount_rows = [
        row
        for row in lineage_rows
        if row[3] == "amount"
    ]

    assert len(amount_rows) == 3

    assert {
        (
            row[0],
            row[1],
        )
        for row in amount_rows
    } == {
        (
            "ods.web_orders",
            "price",
        ),
        (
            "ods.web_orders",
            "quantity",
        ),
        (
            "ods.store_orders",
            "amount",
        ),
    }

    web_amount_rows = [
        row
        for row in amount_rows
        if row[0] == "ods.web_orders"
    ]

    assert len(web_amount_rows) == 2

    assert all(
        row[4] == "transform"
        for row in web_amount_rows
    )

    assert all(
        row[6] == "price * quantity"
        for row in web_amount_rows
    )

    store_amount_rows = [
        row
        for row in amount_rows
        if row[0] == "ods.store_orders"
    ]

    assert len(store_amount_rows) == 1

    assert (
        store_amount_rows[0][4]
        == "direct"
    )

    assert (
        store_amount_rows[0][6]
        == "amount"
    )

    # ========================================================
    # 12. 检查 LineageEvidence
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

    assert len(evidences) == 5

    assert all(
        evidence.column_lineage_id
        is not None
        for evidence in evidences
    )

    assert all(
        evidence.statement_no == 1
        for evidence in evidences
    )

    transformed_evidences = [
        evidence
        for evidence in evidences
        if evidence.expression_text
        == "price * quantity"
    ]

    assert len(transformed_evidences) == 2

    direct_order_id_evidences = [
        evidence
        for evidence in evidences
        if evidence.expression_text
        == "order_id"
    ]

    assert len(
        direct_order_id_evidences
    ) == 2

    direct_amount_evidences = [
        evidence
        for evidence in evidences
        if evidence.expression_text
        == "amount"
    ]

    assert len(
        direct_amount_evidences
    ) == 1

    # ========================================================
    # 13. 第二次导入，验证幂等性
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
    # 14. 检查重新导入后的数量
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

    assert len(lineages_after_reingest) == 5

    evidences_after_reingest = db_session.scalars(
        select(LineageEvidence).where(
            LineageEvidence.script_id
            == script.id
        )
    ).all()

    assert len(evidences_after_reingest) == 5

def test_ingest_cte_union_all_lineage(
    db_session,
    tmp_path,
):
    """
    测试 CTE + UNION ALL 的数据库持久化和重新导入。
    """

    project = create_union_test_project(
        db_session=db_session,
    )

    sql_root = tmp_path / "sql_source"
    hive_directory = sql_root / "hive"

    hive_directory.mkdir(
        parents=True,
    )

    sql_file = (
        hive_directory
        / "cte_union_all_orders.sql"
    )

    sql_file.write_text(
        (
            "WITH web_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.web_orders\n"
            "),\n"
            "store_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        amount\n"
            "    FROM ods.store_orders\n"
            ")\n"
            "INSERT INTO dwd.all_orders (\n"
            "    order_id,\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    amount\n"
            "FROM web_base\n"
            "\n"
            "UNION ALL\n"
            "\n"
            "SELECT\n"
            "    order_id,\n"
            "    amount\n"
            "FROM store_base;\n"
        ),
        encoding="utf-8",
    )

    first_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert first_result.status == "success"
    assert first_result.total_files == 1
    assert first_result.success_files == 1
    assert first_result.failed_files == 0
    assert first_result.created_scripts == 1
    assert first_result.updated_scripts == 0

    # 两张来源表 READ，一张目标表 WRITE。
    assert first_result.table_access_count == 3

    script = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).one()

    assert script.parse_status == "success"
    assert script.dialect == "hive"

    tables = db_session.scalars(
        select(DataTable).where(
            DataTable.project_id
            == project.id
        )
    ).all()

    table_full_names = {
        table.full_name
        for table in tables
    }

    assert table_full_names == {
        "ods.web_orders",
        "ods.store_orders",
        "dwd.all_orders",
    }

    assert "web_base" not in table_full_names
    assert "store_base" not in table_full_names

    assert len(tables) == 3

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

    assert {
        (
            full_name,
            access_type,
        )
        for full_name, access_type
        in access_rows
    } == {
        (
            "ods.web_orders",
            "read",
        ),
        (
            "ods.store_orders",
            "read",
        ),
        (
            "dwd.all_orders",
            "write",
        ),
    }

    # web 3个字段，store 2个字段，target 2个字段。
    columns = db_session.scalars(
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

    assert len(columns) == 7

    lineages = db_session.scalars(
        select(ColumnLineage).where(
            ColumnLineage.project_id
            == project.id,
            ColumnLineage.script_id
            == script.id,
        )
    ).all()

    assert len(lineages) == 5

    evidences = db_session.scalars(
        select(LineageEvidence).where(
            LineageEvidence.script_id
            == script.id
        )
    ).all()

    assert len(evidences) == 5

    # 第二次导入，验证不会重复。
    second_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert second_result.status == "success"
    assert second_result.created_scripts == 0
    assert second_result.updated_scripts == 1

    lineages_after_reingest = db_session.scalars(
        select(ColumnLineage).where(
            ColumnLineage.project_id
            == project.id,
            ColumnLineage.script_id
            == script.id,
        )
    ).all()

    evidences_after_reingest = db_session.scalars(
        select(LineageEvidence).where(
            LineageEvidence.script_id
            == script.id
        )
    ).all()

    assert len(lineages_after_reingest) == 5
    assert len(evidences_after_reingest) == 5
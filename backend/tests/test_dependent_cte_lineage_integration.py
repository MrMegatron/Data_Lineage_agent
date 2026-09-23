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
    创建名称唯一的测试项目。

    uuid 可以防止重复运行测试时，
    项目名称与之前的数据冲突。
    """

    project = LineageProject(
        name=(
            "dependent_cte_integration_"
            f"{uuid4().hex[:12]}"
        ),
        description="多层依赖 CTE 集成测试",
    )

    db_session.add(project)
    db_session.flush()

    assert project.id is not None

    return project


def test_ingest_dependent_cte_lineage(
    db_session,
    tmp_path,
):
    """
    正式测试三层依赖 CTE 的数据库导入。

    SQL 处理链：

        ods.orders
            ↓
        level_one
            ↓
        level_two
            ↓
        level_three
            ↓
        dwd.order_result

    最终字段血缘：

        ods.orders.order_id
            -> dwd.order_result.order_id

        ods.orders.price
            -> dwd.order_result.amount

        ods.orders.quantity
            -> dwd.order_result.amount
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
        / "dependent_cte_order_result.sql"
    )

    # ========================================================
    # 3. 写入三层 CTE SQL
    # ========================================================

    sql_file.write_text(
        (
            "WITH level_one AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.orders\n"
            "),\n"
            "level_two AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        amount\n"
            "    FROM level_one\n"
            "),\n"
            "level_three AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        amount\n"
            "    FROM level_two\n"
            ")\n"
            "INSERT INTO dwd.order_result (\n"
            "    order_id,\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    amount\n"
            "FROM level_three;\n"
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
    # 5. 检查导入结果
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

    # 只应该存在：
    #
    # READ  ods.orders
    # WRITE dwd.order_result
    #
    # level_one、level_two、level_three
    # 都不能作为物理表访问保存。
    assert first_result.table_access_count == 2

    # ========================================================
    # 6. 检查 SourceScript
    # ========================================================

    script = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).one()

    assert script.file_name == (
        "dependent_cte_order_result.sql"
    )

    assert script.relative_path == (
        "hive/dependent_cte_order_result.sql"
    )

    assert script.dialect == "hive"
    assert script.parse_status == "success"
    assert script.parse_error is None

    # ========================================================
    # 7. 检查 DataTable
    #
    # 数据库里只能有两张物理表：
    #
    # ods.orders
    # dwd.order_result
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
        "dwd.order_result",
    }

    assert len(tables) == 2

    # CTE 名称不能成为 DataTable。
    assert "level_one" not in table_full_names
    assert "level_two" not in table_full_names
    assert "level_three" not in table_full_names

    table_by_full_name = {
        table.full_name: table
        for table in tables
    }

    source_table = table_by_full_name[
        "ods.orders"
    ]

    target_table = table_by_full_name[
        "dwd.order_result"
    ]

    # ========================================================
    # 8. 检查 ScriptTableAccess
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
            "dwd.order_result",
            "write",
        ),
    }

    assert len(access_rows) == 2

    # ========================================================
    # 9. 检查 DataColumn
    #
    # 来源表字段：
    #
    # order_id
    # price
    # quantity
    #
    # 目标表字段：
    #
    # order_id
    # amount
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
        "amount",
    }

    # 来源表3个字段 + 目标表2个字段。
    assert len(columns) == 5

    # ========================================================
    # 10. 检查 ColumnLineage
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
    # amount 对应 price 和 quantity 两条。
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
            "dwd.order_result",
            "order_id",
            "direct",
            "confirmed",
            "order_id",
        ),
        (
            "ods.orders",
            "price",
            "dwd.order_result",
            "amount",
            "transform",
            "confirmed",
            "price * quantity",
        ),
        (
            "ods.orders",
            "quantity",
            "dwd.order_result",
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
    # 验证重新导入不会产生重复数据。
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
def test_ingest_transformed_dependent_cte(
    db_session,
    tmp_path,
):
    """
    测试下游 CTE 对上游 CTE 字段继续计算，
    并把最终字段血缘正式保存到数据库。

    第一层：

        price * quantity AS amount

    第二层：

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

    project = create_test_project(
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
        / "dependent_cte_tax.sql"
    )

    sql_file.write_text(
        (
            "WITH order_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.orders\n"
            "),\n"
            "taxed_order AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        amount * 1.1 AS tax_amount\n"
            "    FROM order_base\n"
            ")\n"
            "INSERT INTO dwd.order_tax (\n"
            "    order_id,\n"
            "    tax_amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    tax_amount\n"
            "FROM taxed_order;\n"
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
    # 4. 检查目录导入结果
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
    # 5. 查询 SourceScript
    # ========================================================

    script = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).one()

    assert script.file_name == (
        "dependent_cte_tax.sql"
    )

    assert script.relative_path == (
        "hive/dependent_cte_tax.sql"
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
    # order_base
    # taxed_order
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

    assert "order_base" not in table_full_names
    assert "taxed_order" not in table_full_names

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
    # 来源字段：
    #
    # order_id
    # price
    # quantity
    #
    # 目标字段：
    #
    # order_id
    # tax_amount
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
    # tax_amount 两条。
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
            "amount * 1.1",
        ),
        (
            "ods.orders",
            "quantity",
            "dwd.order_tax",
            "tax_amount",
            "transform",
            "confirmed",
            "amount * 1.1",
        ),
    }

    # ========================================================
    # 10. 单独检查 tax_amount 字段血缘
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
        row[6] == "amount * 1.1"
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
        == "amount * 1.1"
    ]

    # price -> tax_amount 一条证据；
    # quantity -> tax_amount 一条证据。
    assert len(tax_amount_evidences) == 2

    assert all(
        evidence.code_snippet
        for evidence in tax_amount_evidences
    )

    # ========================================================
    # 12. 第二次导入
    #
    # 验证不会产生重复数据。
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
    # 13. 检查重新导入后的数据数量
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

def test_ingest_aggregated_dependent_cte(
    db_session,
    tmp_path,
):
    """
    测试下游 CTE 聚合上游 CTE 的计算字段，
    并把最终血缘正式保存到数据库。

    第一层：

        price * quantity AS amount

    第二层：

        SUM(amount) AS total_amount

    最终物理来源：

        ods.orders.price
        ods.orders.quantity

    最终目标：

        ads.customer_summary.total_amount
    """

    # ========================================================
    # 1. 创建测试项目
    # ========================================================

    project = create_test_project(
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
        / "customer_summary.sql"
    )

    sql_file.write_text(
        (
            "WITH order_base AS (\n"
            "    SELECT\n"
            "        customer_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.orders\n"
            "),\n"
            "customer_summary AS (\n"
            "    SELECT\n"
            "        customer_id,\n"
            "        SUM(amount) AS total_amount\n"
            "    FROM order_base\n"
            "    GROUP BY customer_id\n"
            ")\n"
            "INSERT INTO ads.customer_summary (\n"
            "    customer_id,\n"
            "    total_amount\n"
            ")\n"
            "SELECT\n"
            "    customer_id,\n"
            "    total_amount\n"
            "FROM customer_summary;\n"
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
    # WRITE ads.customer_summary
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
        "customer_summary.sql"
    )

    assert script.relative_path == (
        "hive/customer_summary.sql"
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
    # ads.customer_summary
    #
    # 不能保存：
    #
    # order_base
    # customer_summary（CTE）
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
        "ads.customer_summary",
    }

    assert len(tables) == 2

    assert "order_base" not in table_full_names

    # 目标物理表的完整名称是：
    #
    #     ads.customer_summary
    #
    # CTE 名称只是：
    #
    #     customer_summary
    #
    # 不能额外出现没有 schema 的表。
    assert "customer_summary" not in (
        table_full_names
    )

    table_by_full_name = {
        table.full_name: table
        for table in tables
    }

    source_table = table_by_full_name[
        "ods.orders"
    ]

    target_table = table_by_full_name[
        "ads.customer_summary"
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
            "ads.customer_summary",
            "write",
        ),
    }

    assert len(access_rows) == 2

    # ========================================================
    # 8. 检查 DataColumn
    #
    # 来源字段：
    #
    # customer_id
    # price
    # quantity
    #
    # 目标字段：
    #
    # customer_id
    # total_amount
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
        "customer_id",
        "price",
        "quantity",
    }

    assert column_names_by_table_id[
        target_table.id
    ] == {
        "customer_id",
        "total_amount",
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

    # customer_id 一条；
    # total_amount 对应 price 和 quantity 两条。
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
            "customer_id",
            "ads.customer_summary",
            "customer_id",
            "direct",
            "confirmed",
            "customer_id",
        ),
        (
            "ods.orders",
            "price",
            "ads.customer_summary",
            "total_amount",
            "aggregate",
            "confirmed",
            "SUM(amount)",
        ),
        (
            "ods.orders",
            "quantity",
            "ads.customer_summary",
            "total_amount",
            "aggregate",
            "confirmed",
            "SUM(amount)",
        ),
    }

    # ========================================================
    # 10. 单独检查 total_amount
    # ========================================================

    total_amount_rows = [
        row
        for row in lineage_rows
        if row[3] == "total_amount"
    ]

    assert len(total_amount_rows) == 2

    assert {
        row[1]
        for row in total_amount_rows
    } == {
        "price",
        "quantity",
    }

    assert all(
        row[0] == "ods.orders"
        for row in total_amount_rows
    )

    assert all(
        row[2] == "ads.customer_summary"
        for row in total_amount_rows
    )

    assert all(
        row[4] == "aggregate"
        for row in total_amount_rows
    )

    assert all(
        row[5] == "confirmed"
        for row in total_amount_rows
    )

    assert all(
        row[6] == "SUM(amount)"
        for row in total_amount_rows
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

    aggregate_evidences = [
        evidence
        for evidence in evidences
        if evidence.expression_text
        == "SUM(amount)"
    ]

    # price -> total_amount 一条证据；
    # quantity -> total_amount 一条证据。
    assert len(aggregate_evidences) == 2

    assert all(
        evidence.code_snippet
        for evidence in aggregate_evidences
    )

    # ========================================================
    # 12. 第二次导入
    #
    # 检查幂等性。
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

def test_ingest_multiple_upstream_cte_transform(
    db_session,
    tmp_path,
):
    """
    测试一个下游 CTE 同时读取两个上游 CTE，
    并组合两边字段进行计算。

    订单金额：

        ods.orders.price
        *
        ods.orders.quantity
        =
        order_base.amount

    退款金额：

        ods.refunds.refund_amount
        =
        refund_base.refund_amount

    净金额：

        order_base.amount
        -
        refund_base.refund_amount
        =
        ads.order_net.net_amount
    """

    # ========================================================
    # 1. 创建测试项目
    # ========================================================

    project = create_test_project(
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
        / "order_net.sql"
    )

    sql_file.write_text(
        (
            "WITH order_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.orders\n"
            "),\n"
            "refund_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        refund_amount\n"
            "    FROM ods.refunds\n"
            "),\n"
            "order_net AS (\n"
            "    SELECT\n"
            "        o.order_id,\n"
            "        o.amount - r.refund_amount AS net_amount\n"
            "    FROM order_base AS o\n"
            "    JOIN refund_base AS r\n"
            "      ON o.order_id = r.order_id\n"
            ")\n"
            "INSERT INTO ads.order_net (\n"
            "    order_id,\n"
            "    net_amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    net_amount\n"
            "FROM order_net;\n"
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
    # 4. 检查目录导入结果
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
    # READ  ods.refunds
    # WRITE ads.order_net
    assert first_result.table_access_count == 3

    # ========================================================
    # 5. 查询 SourceScript
    # ========================================================

    script = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).one()

    assert script.file_name == "order_net.sql"

    assert script.relative_path == (
        "hive/order_net.sql"
    )

    assert script.dialect == "hive"
    assert script.parse_status == "success"
    assert script.parse_error is None

    # ========================================================
    # 6. 检查 DataTable
    #
    # 只能保存三张物理表：
    #
    # ods.orders
    # ods.refunds
    # ads.order_net
    #
    # 三个 CTE 不能保存成物理表。
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
        "ods.refunds",
        "ads.order_net",
    }

    assert len(tables) == 3

    assert "order_base" not in table_full_names
    assert "refund_base" not in table_full_names
    assert "order_net" not in table_full_names

    table_by_full_name = {
        table.full_name: table
        for table in tables
    }

    orders_table = table_by_full_name[
        "ods.orders"
    ]

    refunds_table = table_by_full_name[
        "ods.refunds"
    ]

    target_table = table_by_full_name[
        "ads.order_net"
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
            "ods.refunds",
            "read",
        ),
        (
            "ads.order_net",
            "write",
        ),
    }

    assert len(access_rows) == 3

    # ========================================================
    # 8. 检查 DataColumn
    #
    # ods.orders：
    #
    #     order_id
    #     price
    #     quantity
    #
    # ods.refunds：
    #
    #     refund_amount
    #
    # ads.order_net：
    #
    #     order_id
    #     net_amount
    #
    # 注意：
    #
    # ods.refunds.order_id 只出现在 JOIN 条件里，
    # 当前阶段不属于目标字段值血缘，所以不保存。
    # ========================================================

    columns = db_session.scalars(
        select(DataColumn)
        .where(
            DataColumn.table_id.in_(
                [
                    orders_table.id,
                    refunds_table.id,
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
        orders_table.id
    ] == {
        "order_id",
        "price",
        "quantity",
    }

    assert column_names_by_table_id[
        refunds_table.id
    ] == {
        "refund_amount",
    }

    assert column_names_by_table_id[
        target_table.id
    ] == {
        "order_id",
        "net_amount",
    }

    assert "order_id" not in (
        column_names_by_table_id[
            refunds_table.id
        ]
    )

    # 3 + 1 + 2 = 6
    assert len(columns) == 6

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
    # net_amount 三条。
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
            "ads.order_net",
            "order_id",
            "direct",
            "confirmed",
            "order_id",
        ),
        (
            "ods.orders",
            "price",
            "ads.order_net",
            "net_amount",
            "transform",
            "confirmed",
            "o.amount - r.refund_amount",
        ),
        (
            "ods.orders",
            "quantity",
            "ads.order_net",
            "net_amount",
            "transform",
            "confirmed",
            "o.amount - r.refund_amount",
        ),
        (
            "ods.refunds",
            "refund_amount",
            "ads.order_net",
            "net_amount",
            "transform",
            "confirmed",
            "o.amount - r.refund_amount",
        ),
    }

    # ========================================================
    # 10. 单独检查 net_amount
    # ========================================================

    net_amount_rows = [
        row
        for row in lineage_rows
        if row[3] == "net_amount"
    ]

    assert len(net_amount_rows) == 3

    actual_net_sources = {
        (
            row[0],
            row[1],
        )
        for row in net_amount_rows
    }

    assert actual_net_sources == {
        (
            "ods.orders",
            "price",
        ),
        (
            "ods.orders",
            "quantity",
        ),
        (
            "ods.refunds",
            "refund_amount",
        ),
    }

    assert all(
        row[2] == "ads.order_net"
        for row in net_amount_rows
    )

    assert all(
        row[4] == "transform"
        for row in net_amount_rows
    )

    assert all(
        row[5] == "confirmed"
        for row in net_amount_rows
    )

    assert all(
        row[6]
        == "o.amount - r.refund_amount"
        for row in net_amount_rows
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

    net_amount_evidences = [
        evidence
        for evidence in evidences
        if evidence.expression_text
        == "o.amount - r.refund_amount"
    ]

    # net_amount 有三个来源字段，
    # 所以对应三条代码证据。
    assert len(net_amount_evidences) == 3

    assert all(
        evidence.code_snippet
        for evidence in net_amount_evidences
    )

    # ========================================================
    # 12. 第二次导入，检查幂等性
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
    # 13. 检查重新导入后没有重复
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

    assert len(columns_after_reingest) == 6

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

def test_ingest_case_when_multiple_upstream_ctes(
    db_session,
    tmp_path,
):
    """
    测试跨多个上游 CTE 的 CASE WHEN 字段血缘，
    并验证正式数据库持久化。

    订单金额：

        ods.orders.price
        *
        ods.orders.quantity
        =
        order_base.amount

    退款数据：

        ods.refunds.refund_status
        ods.refunds.refund_amount

    最终计算：

        CASE
            WHEN refund_status = 'approved'
            THEN amount - refund_amount
            ELSE amount
        END AS net_amount

    net_amount 的字段依赖：

        ods.orders.price
        ods.orders.quantity
        ods.refunds.refund_status
        ods.refunds.refund_amount

    JOIN 条件中的：

        ods.refunds.order_id

    不属于 net_amount 的值字段血缘。
    """

    # ========================================================
    # 1. 创建测试项目
    # ========================================================

    project = create_test_project(
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
        / "case_when_order_net.sql"
    )

    sql_file.write_text(
        (
            "WITH order_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.orders\n"
            "),\n"
            "refund_base AS (\n"
            "    SELECT\n"
            "        order_id,\n"
            "        refund_status,\n"
            "        refund_amount\n"
            "    FROM ods.refunds\n"
            "),\n"
            "order_net AS (\n"
            "    SELECT\n"
            "        o.order_id,\n"
            "        CASE\n"
            "            WHEN r.refund_status = 'approved'\n"
            "            THEN o.amount - r.refund_amount\n"
            "            ELSE o.amount\n"
            "        END AS net_amount\n"
            "    FROM order_base AS o\n"
            "    LEFT JOIN refund_base AS r\n"
            "      ON o.order_id = r.order_id\n"
            ")\n"
            "INSERT INTO ads.order_net (\n"
            "    order_id,\n"
            "    net_amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    net_amount\n"
            "FROM order_net;\n"
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
    # READ  ods.refunds
    # WRITE ads.order_net
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
        "case_when_order_net.sql"
    )

    assert script.relative_path == (
        "hive/case_when_order_net.sql"
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
        "ods.orders",
        "ods.refunds",
        "ads.order_net",
    }

    assert len(tables) == 3

    # CTE 名称不能成为物理表。
    assert "order_base" not in table_full_names
    assert "refund_base" not in table_full_names
    assert "order_net" not in table_full_names

    table_by_full_name = {
        table.full_name: table
        for table in tables
    }

    orders_table = table_by_full_name[
        "ods.orders"
    ]

    refunds_table = table_by_full_name[
        "ods.refunds"
    ]

    target_table = table_by_full_name[
        "ads.order_net"
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
            "ods.refunds",
            "read",
        ),
        (
            "ads.order_net",
            "write",
        ),
    }

    assert len(access_rows) == 3

    # ========================================================
    # 8. 检查 DataColumn
    #
    # ods.orders：
    #
    #     order_id
    #     price
    #     quantity
    #
    # ods.refunds：
    #
    #     refund_status
    #     refund_amount
    #
    # ads.order_net：
    #
    #     order_id
    #     net_amount
    #
    # 共7个字段。
    # ========================================================

    columns = db_session.scalars(
        select(DataColumn)
        .where(
            DataColumn.table_id.in_(
                [
                    orders_table.id,
                    refunds_table.id,
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
        orders_table.id
    ] == {
        "order_id",
        "price",
        "quantity",
    }

    assert column_names_by_table_id[
        refunds_table.id
    ] == {
        "refund_status",
        "refund_amount",
    }

    assert column_names_by_table_id[
        target_table.id
    ] == {
        "order_id",
        "net_amount",
    }

    # refunds.order_id 只出现在 JOIN ON 中，
    # 不属于当前值字段血缘。
    assert "order_id" not in (
        column_names_by_table_id[
            refunds_table.id
        ]
    )

    assert len(columns) == 7

    # ========================================================
    # 9. 查询 ColumnLineage
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

    # order_id：1条
    #
    # net_amount：
    #     price
    #     quantity
    #     refund_status
    #     refund_amount
    #
    # 共5条。
    assert len(lineage_rows) == 5

    expected_expression = (
        "CASE "
        "WHEN r.refund_status = 'approved' "
        "THEN o.amount - r.refund_amount "
        "ELSE o.amount "
        "END"
    )

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
            "ads.order_net",
            "order_id",
            "direct",
            "confirmed",
            "order_id",
        ),
        (
            "ods.orders",
            "price",
            "ads.order_net",
            "net_amount",
            "transform",
            "confirmed",
            expected_expression,
        ),
        (
            "ods.orders",
            "quantity",
            "ads.order_net",
            "net_amount",
            "transform",
            "confirmed",
            expected_expression,
        ),
        (
            "ods.refunds",
            "refund_status",
            "ads.order_net",
            "net_amount",
            "transform",
            "confirmed",
            expected_expression,
        ),
        (
            "ods.refunds",
            "refund_amount",
            "ads.order_net",
            "net_amount",
            "transform",
            "confirmed",
            expected_expression,
        ),
    }

    # ========================================================
    # 10. 单独检查 net_amount
    # ========================================================

    net_amount_rows = [
        row
        for row in lineage_rows
        if row[3] == "net_amount"
    ]

    assert len(net_amount_rows) == 4

    actual_net_sources = {
        (
            row[0],
            row[1],
        )
        for row in net_amount_rows
    }

    assert actual_net_sources == {
        (
            "ods.orders",
            "price",
        ),
        (
            "ods.orders",
            "quantity",
        ),
        (
            "ods.refunds",
            "refund_status",
        ),
        (
            "ods.refunds",
            "refund_amount",
        ),
    }

    assert (
        "ods.refunds",
        "order_id",
    ) not in actual_net_sources

    assert all(
        row[2] == "ads.order_net"
        for row in net_amount_rows
    )

    assert all(
        row[4] == "transform"
        for row in net_amount_rows
    )

    assert all(
        row[5] == "confirmed"
        for row in net_amount_rows
    )

    assert all(
        row[6] == expected_expression
        for row in net_amount_rows
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

    case_evidences = [
        evidence
        for evidence in evidences
        if evidence.expression_text
        == expected_expression
    ]

    # net_amount 有四个依赖字段，
    # 因此对应四条证据记录。
    assert len(case_evidences) == 4

    assert all(
        evidence.code_snippet
        for evidence in case_evidences
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
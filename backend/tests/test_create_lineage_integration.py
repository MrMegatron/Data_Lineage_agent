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


def create_create_lineage_project(
    db_session,
    prefix: str,
) -> LineageProject:
    """
    创建名称唯一的测试项目。
    """

    project = LineageProject(
        name=(
            f"{prefix}_"
            f"{uuid4().hex[:12]}"
        ),
        description=(
            "CREATE TABLE/VIEW "
            "字段血缘集成测试"
        ),
    )

    db_session.add(project)
    db_session.flush()

    assert project.id is not None

    return project


def load_project_tables(
    db_session,
    project_id: int,
) -> list[DataTable]:
    """
    查询项目下的全部数据表。
    """

    return list(
        db_session.scalars(
            select(DataTable)
            .where(
                DataTable.project_id
                == project_id
            )
            .order_by(
                DataTable.full_name
            )
        ).all()
    )


def load_script_accesses(
    db_session,
    script_id: int,
) -> set[tuple[str, str]]:
    """
    查询脚本的 READ / WRITE 表访问。
    """

    rows = db_session.execute(
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
            == script_id
        )
    ).all()

    return {
        (
            full_name,
            access_type,
        )
        for (
            full_name,
            access_type,
        ) in rows
    }


def load_lineage_rows(
    db_session,
    *,
    project_id: int,
    script_id: int,
) -> list:
    """
    查询带有来源表、来源字段、目标表和目标字段的
    完整字段血缘。
    """

    source_column = aliased(
        DataColumn
    )

    source_table = aliased(
        DataTable
    )

    target_column = aliased(
        DataColumn
    )

    target_table = aliased(
        DataTable
    )

    return list(
        db_session.execute(
            select(
                source_table.full_name,
                source_column.column_name,
                target_table.full_name,
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
                source_table,
                source_table.id
                == source_column.table_id,
            )
            .join(
                target_column,
                target_column.id
                == ColumnLineage.target_column_id,
            )
            .join(
                target_table,
                target_table.id
                == target_column.table_id,
            )
            .where(
                ColumnLineage.project_id
                == project_id,
                ColumnLineage.script_id
                == script_id,
            )
        ).all()
    )


def test_ingest_create_table_as_select(
    db_session,
    tmp_path,
):
    """
    测试 CREATE TABLE AS SELECT。

    预期：

        READ  ods.orders
        WRITE dwd.order_detail

    字段血缘：

        ods.orders.order_id
            -> dwd.order_detail.order_id

        ods.orders.price
            -> dwd.order_detail.amount

        ods.orders.quantity
            -> dwd.order_detail.amount
    """

    # ========================================================
    # 1. 创建项目和 SQL 文件
    # ========================================================

    project = create_create_lineage_project(
        db_session=db_session,
        prefix="ctas",
    )

    sql_root = tmp_path / "ctas_source"
    hive_directory = sql_root / "hive"

    hive_directory.mkdir(
        parents=True,
    )

    sql_file = (
        hive_directory
        / "create_order_detail.sql"
    )

    sql_file.write_text(
        (
            "CREATE TABLE dwd.order_detail AS\n"
            "SELECT\n"
            "    order_id,\n"
            "    price * quantity AS amount\n"
            "FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    # ========================================================
    # 2. 第一次导入
    # ========================================================

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

    assert first_result.total_statements == 1
    assert first_result.successful_statements == 1
    assert first_result.failed_statements == 0

    # READ ods.orders + WRITE dwd.order_detail
    assert first_result.table_access_count == 2

    # ========================================================
    # 3. 检查脚本
    # ========================================================

    script = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).one()

    assert script.parse_status == "success"
    assert script.parse_error is None
    assert script.dialect == "hive"

    # ========================================================
    # 4. 检查表
    # ========================================================

    tables = load_project_tables(
        db_session=db_session,
        project_id=project.id,
    )

    assert {
        table.full_name
        for table in tables
    } == {
        "ods.orders",
        "dwd.order_detail",
    }

    assert len(tables) == 2

    # ========================================================
    # 5. 检查 READ / WRITE
    # ========================================================

    assert load_script_accesses(
        db_session=db_session,
        script_id=script.id,
    ) == {
        (
            "ods.orders",
            "read",
        ),
        (
            "dwd.order_detail",
            "write",
        ),
    }

    # ========================================================
    # 6. 检查字段
    # ========================================================

    table_by_name = {
        table.full_name: table
        for table in tables
    }

    source_table = table_by_name[
        "ods.orders"
    ]

    target_table = table_by_name[
        "dwd.order_detail"
    ]

    source_columns = db_session.scalars(
        select(DataColumn).where(
            DataColumn.table_id
            == source_table.id
        )
    ).all()

    target_columns = db_session.scalars(
        select(DataColumn).where(
            DataColumn.table_id
            == target_table.id
        )
    ).all()

    assert {
        column.column_name
        for column in source_columns
    } == {
        "order_id",
        "price",
        "quantity",
    }

    assert {
        column.column_name
        for column in target_columns
    } == {
        "order_id",
        "amount",
    }

    assert (
        len(source_columns)
        + len(target_columns)
    ) == 5

    # ========================================================
    # 7. 检查字段血缘
    # ========================================================

    lineage_rows = load_lineage_rows(
        db_session,
        project_id=project.id,
        script_id=script.id,
    )

    assert len(lineage_rows) == 3

    assert {
        tuple(row)
        for row in lineage_rows
    } == {
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
    # 8. 检查代码证据
    # ========================================================

    evidences = db_session.scalars(
        select(LineageEvidence).where(
            LineageEvidence.script_id
            == script.id
        )
    ).all()

    assert len(evidences) == 3

    assert {
        evidence.expression_text
        for evidence in evidences
    } == {
        "order_id",
        "price * quantity",
    }

    # ========================================================
    # 9. 重新导入
    # ========================================================

    second_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert second_result.status == "success"
    assert second_result.created_scripts == 0
    assert second_result.updated_scripts == 1

    assert len(
        db_session.scalars(
            select(ColumnLineage).where(
                ColumnLineage.project_id
                == project.id
            )
        ).all()
    ) == 3

    assert len(
        db_session.scalars(
            select(LineageEvidence).where(
                LineageEvidence.script_id
                == script.id
            )
        ).all()
    ) == 3


def test_ingest_create_view_as_cte(
    db_session,
    tmp_path,
):
    """
    测试 CREATE VIEW AS CTE SELECT。

    预期：

        READ  ods.orders
        WRITE ads.order_summary

    order_base 是 CTE，不能成为物理表。
    """

    # ========================================================
    # 1. 创建项目和 SQL 文件
    # ========================================================

    project = create_create_lineage_project(
        db_session=db_session,
        prefix="create_view",
    )

    sql_root = tmp_path / "view_source"
    hive_directory = sql_root / "hive"

    hive_directory.mkdir(
        parents=True,
    )

    sql_file = (
        hive_directory
        / "create_order_summary_view.sql"
    )

    sql_file.write_text(
        (
            "CREATE VIEW ads.order_summary AS\n"
            "WITH order_base AS (\n"
            "    SELECT\n"
            "        customer_id,\n"
            "        price * quantity AS amount\n"
            "    FROM ods.orders\n"
            ")\n"
            "SELECT\n"
            "    customer_id,\n"
            "    SUM(amount) AS total_amount\n"
            "FROM order_base\n"
            "GROUP BY customer_id;\n"
        ),
        encoding="utf-8",
    )

    # ========================================================
    # 2. 第一次导入
    # ========================================================

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

    assert first_result.total_statements == 1
    assert first_result.successful_statements == 1
    assert first_result.failed_statements == 0

    assert first_result.table_access_count == 2

    # ========================================================
    # 3. 检查脚本和物理表
    # ========================================================

    script = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).one()

    assert script.parse_status == "success"
    assert script.parse_error is None

    tables = load_project_tables(
        db_session=db_session,
        project_id=project.id,
    )

    table_full_names = {
        table.full_name
        for table in tables
    }

    assert table_full_names == {
        "ods.orders",
        "ads.order_summary",
    }

    assert "order_base" not in (
        table_full_names
    )

    assert len(tables) == 2

    # ========================================================
    # 4. 检查 READ / WRITE
    # ========================================================

    assert load_script_accesses(
        db_session=db_session,
        script_id=script.id,
    ) == {
        (
            "ods.orders",
            "read",
        ),
        (
            "ads.order_summary",
            "write",
        ),
    }

    # ========================================================
    # 5. 检查字段
    # ========================================================

    table_by_name = {
        table.full_name: table
        for table in tables
    }

    source_table = table_by_name[
        "ods.orders"
    ]

    target_table = table_by_name[
        "ads.order_summary"
    ]

    source_columns = db_session.scalars(
        select(DataColumn).where(
            DataColumn.table_id
            == source_table.id
        )
    ).all()

    target_columns = db_session.scalars(
        select(DataColumn).where(
            DataColumn.table_id
            == target_table.id
        )
    ).all()

    assert {
        column.column_name
        for column in source_columns
    } == {
        "customer_id",
        "price",
        "quantity",
    }

    assert {
        column.column_name
        for column in target_columns
    } == {
        "customer_id",
        "total_amount",
    }

    assert (
        len(source_columns)
        + len(target_columns)
    ) == 5

    # ========================================================
    # 6. 检查字段血缘
    # ========================================================

    lineage_rows = load_lineage_rows(
        db_session,
        project_id=project.id,
        script_id=script.id,
    )

    assert len(lineage_rows) == 3

    assert {
        tuple(row)
        for row in lineage_rows
    } == {
        (
            "ods.orders",
            "customer_id",
            "ads.order_summary",
            "customer_id",
            "direct",
            "confirmed",
            "customer_id",
        ),
        (
            "ods.orders",
            "price",
            "ads.order_summary",
            "total_amount",
            "aggregate",
            "confirmed",
            "SUM(amount)",
        ),
        (
            "ods.orders",
            "quantity",
            "ads.order_summary",
            "total_amount",
            "aggregate",
            "confirmed",
            "SUM(amount)",
        ),
    }

    # ========================================================
    # 7. 检查证据
    # ========================================================

    evidences = db_session.scalars(
        select(LineageEvidence).where(
            LineageEvidence.script_id
            == script.id
        )
    ).all()

    assert len(evidences) == 3

    assert len([
        evidence
        for evidence in evidences
        if evidence.expression_text
        == "SUM(amount)"
    ]) == 2

    # ========================================================
    # 8. 重新导入
    # ========================================================

    second_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert second_result.status == "success"
    assert second_result.created_scripts == 0
    assert second_result.updated_scripts == 1

    assert len(
        db_session.scalars(
            select(ColumnLineage).where(
                ColumnLineage.project_id
                == project.id
            )
        ).all()
    ) == 3

    assert len(
        db_session.scalars(
            select(LineageEvidence).where(
                LineageEvidence.script_id
                == script.id
            )
        ).all()
    ) == 3
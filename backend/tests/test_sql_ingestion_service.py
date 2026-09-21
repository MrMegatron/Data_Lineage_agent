from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    DataTable,
    LineageProject,
    ScriptTableAccess,
    SourceScript,
)
from app.services.sql_file_scanner import (
    scan_sql_files,
)
from app.services.sql_ingestion_service import (
    ingest_scanned_sql_file,
)


# ============================================================
# 测试辅助函数：创建项目
# ============================================================

def create_test_project(
    db_session,
    prefix: str,
) -> LineageProject:
    """
    创建一个测试项目。

    uuid 用于避免多次执行测试时名称重复。
    """

    project = LineageProject(
        name=(
            f"{prefix}_{uuid4().hex[:8]}"
        ),
        description="SQL ingestion pytest",
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 1. 测试完整导入链路
# ============================================================

def test_ingest_sql_file_to_database(
    db_session,
    tmp_path,
):
    """
    验证：

    SQL 文件
        ↓
    SourceScript
        ↓
    DataTable
        ↓
    ScriptTableAccess
    """

    project = create_test_project(
        db_session,
        "ingestion_project",
    )

    sql_root = tmp_path / "sql_source"
    hive_directory = sql_root / "hive"

    hive_directory.mkdir(parents=True)

    sql_file = (
        hive_directory
        / "orders.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.orders\n"
            "SELECT *\n"
            "FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    scanned_files = scan_sql_files(
        sql_root
    )

    assert len(scanned_files) == 1

    result = ingest_scanned_sql_file(
        db=db_session,
        project_id=project.id,
        scanned_file=scanned_files[0],
    )

    # --------------------------------------------------------
    # 检查导入摘要
    # --------------------------------------------------------

    assert result.script_id is not None
    assert result.created_script is True
    assert result.relative_path == (
        "hive/orders.sql"
    )
    assert result.dialect == "hive"
    assert result.parse_status == "success"
    assert result.statement_count == 1
    assert result.success_statement_count == 1
    assert result.failed_statement_count == 0
    assert result.table_access_count == 2

    # --------------------------------------------------------
    # 检查 SourceScript
    # --------------------------------------------------------

    source_script = db_session.get(
        SourceScript,
        result.script_id,
    )

    assert source_script is not None
    assert source_script.project_id == project.id
    assert source_script.file_name == "orders.sql"

    assert source_script.relative_path == (
        "hive/orders.sql"
    )

    assert source_script.dialect == "hive"
    assert source_script.parse_status == "success"
    assert source_script.parse_error is None
    assert len(source_script.file_hash) == 64

    assert "INSERT INTO" in (
        source_script.source_code
    )

    # --------------------------------------------------------
    # 检查 DataTable
    # --------------------------------------------------------

    data_tables = db_session.scalars(
        select(DataTable).where(
            DataTable.project_id
            == project.id
        )
    ).all()

    assert {
        data_table.full_name
        for data_table in data_tables
    } == {
        "ods.orders",
        "dwd.orders",
    }

    assert all(
        data_table.table_kind == "unknown"
        for data_table in data_tables
    )

    # --------------------------------------------------------
    # 检查 ScriptTableAccess
    # --------------------------------------------------------

    table_accesses = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id
            == result.script_id
        )
    ).all()

    assert {
        (
            table_access.access_type,
            table_access.table.full_name,
        )
        for table_access in table_accesses
    } == {
        ("read", "ods.orders"),
        ("write", "dwd.orders"),
    }

    assert all(
        table_access.statement_no == 1
        for table_access in table_accesses
    )


# ============================================================
# 2. 测试重复导入时更新原脚本
# ============================================================

def test_reingest_updates_existing_script(
    db_session,
    tmp_path,
):
    """
    同一个：

        project_id + relative_path

    再次导入时必须更新原 SourceScript，
    不能插入第二条脚本记录。
    """

    project = create_test_project(
        db_session,
        "reingestion_project",
    )

    sql_root = tmp_path / "sql_source"
    hive_directory = sql_root / "hive"

    hive_directory.mkdir(parents=True)

    sql_file = (
        hive_directory
        / "daily_order.sql"
    )

    # --------------------------------------------------------
    # 第一次导入
    # --------------------------------------------------------

    sql_file.write_text(
        (
            "INSERT INTO dwd.orders\n"
            "SELECT * FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    first_scanned_file = scan_sql_files(
        sql_root
    )[0]

    first_result = ingest_scanned_sql_file(
        db=db_session,
        project_id=project.id,
        scanned_file=first_scanned_file,
    )

    first_script_id = (
        first_result.script_id
    )

    assert first_result.created_script is True

    # --------------------------------------------------------
    # 修改 SQL 后第二次导入
    # --------------------------------------------------------

    sql_file.write_text(
        (
            "INSERT INTO ads.order_summary\n"
            "SELECT * FROM dwd.orders;\n"
        ),
        encoding="utf-8",
    )

    second_scanned_file = scan_sql_files(
        sql_root
    )[0]

    second_result = ingest_scanned_sql_file(
        db=db_session,
        project_id=project.id,
        scanned_file=second_scanned_file,
    )

    assert second_result.created_script is False

    # 必须还是原来那条 SourceScript。
    assert second_result.script_id == (
        first_script_id
    )

    # --------------------------------------------------------
    # 项目中只能有一条相同路径的脚本
    # --------------------------------------------------------

    scripts = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id,

            SourceScript.relative_path
            == "hive/daily_order.sql",
        )
    ).all()

    assert len(scripts) == 1

    assert "ads.order_summary" in (
        scripts[0].source_code
    )

    # --------------------------------------------------------
    # 旧访问必须已经被替换
    # --------------------------------------------------------

    current_accesses = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id
            == first_script_id
        )
    ).all()

    assert {
        (
            access.access_type,
            access.table.full_name,
        )
        for access in current_accesses
    } == {
        ("read", "dwd.orders"),
        ("write", "ads.order_summary"),
    }

    old_access_pairs = {
        (
            access.access_type,
            access.table.full_name,
        )
        for access in current_accesses
    }

    assert (
        "read",
        "ods.orders",
    ) not in old_access_pairs


# ============================================================
# 3. 测试解析失败状态写入数据库
# ============================================================

def test_failed_parse_is_saved(
    db_session,
    tmp_path,
):
    """
    SQL 解析失败时：

    1. SourceScript 仍然保存；
    2. parse_status 为 failed；
    3. parse_error 有内容；
    4. 不伪造 READ/WRITE。
    """

    project = create_test_project(
        db_session,
        "failed_parse_project",
    )

    sql_root = tmp_path / "sql_source"
    hive_directory = sql_root / "hive"

    hive_directory.mkdir(parents=True)

    sql_file = (
        hive_directory
        / "broken.sql"
    )

    sql_file.write_text(
        "SELECT (1 +;",
        encoding="utf-8",
    )

    scanned_file = scan_sql_files(
        sql_root
    )[0]

    result = ingest_scanned_sql_file(
        db=db_session,
        project_id=project.id,
        scanned_file=scanned_file,
    )

    assert result.parse_status == "failed"
    assert result.failed_statement_count == 1
    assert result.table_access_count == 0

    source_script = db_session.get(
        SourceScript,
        result.script_id,
    )

    assert source_script is not None
    assert source_script.parse_status == "failed"
    assert source_script.parse_error is not None

    accesses = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id
            == result.script_id
        )
    ).all()

    assert accesses == []


# ============================================================
# 4. 测试项目不存在
# ============================================================

def test_ingest_rejects_missing_project(
    db_session,
    tmp_path,
):
    """
    不允许把 SQL 文件导入一个不存在的项目。
    """

    sql_root = tmp_path / "sql_source"
    sql_root.mkdir()

    sql_file = sql_root / "orders.sql"

    sql_file.write_text(
        "SELECT * FROM ods.orders;",
        encoding="utf-8",
    )

    scanned_file = scan_sql_files(
        sql_root
    )[0]

    with pytest.raises(
        ValueError,
        match="血缘项目不存在",
    ):
        ingest_scanned_sql_file(
            db=db_session,
            project_id=-1,
            scanned_file=scanned_file,
        )
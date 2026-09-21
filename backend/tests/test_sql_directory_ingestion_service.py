from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)
from app.services.sql_directory_ingestion_service import (
    ingest_sql_directory,
)


# ============================================================
# 测试辅助函数：创建项目
# ============================================================

def create_test_project(
    db_session,
    prefix: str,
) -> LineageProject:
    project = LineageProject(
        name=(
            f"{prefix}_{uuid4().hex[:8]}"
        ),
        description=(
            "directory ingestion pytest"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 1. 测试批量导入多个 SQL 文件
# ============================================================

def test_ingest_multiple_sql_files(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session,
        "multiple_files_project",
    )

    sql_root = tmp_path / "sql_source"

    hive_directory = (
        sql_root
        / "hive"
    )

    hive_directory.mkdir(
        parents=True
    )

    # --------------------------------------------------------
    # 创建第一份 SQL
    # --------------------------------------------------------

    first_file = (
        hive_directory
        / "01_ods_to_dwd.sql"
    )

    first_file.write_text(
        (
            "INSERT INTO dwd.orders\n"
            "SELECT * FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # 创建第二份 SQL
    # --------------------------------------------------------

    second_file = (
        hive_directory
        / "02_dwd_to_ads.sql"
    )

    second_file.write_text(
        (
            "INSERT INTO ads.order_summary\n"
            "SELECT * FROM dwd.orders;\n"
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # 创建一个应该被忽略的非 SQL 文件
    # --------------------------------------------------------

    readme_file = (
        sql_root
        / "readme.txt"
    )

    readme_file.write_text(
        "这个文件不应该被导入",
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # 先验证测试文件确实已经创建
    # --------------------------------------------------------

    assert first_file.exists()
    assert second_file.exists()
    assert readme_file.exists()

    assert first_file.is_file()
    assert second_file.is_file()

    # --------------------------------------------------------
    # 执行目录导入
    # --------------------------------------------------------

    result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    # --------------------------------------------------------
    # 检查目录汇总结果
    # --------------------------------------------------------

    assert result.status == "success"

    assert result.total_files == 2
    assert result.success_files == 2
    assert result.failed_files == 0

    assert result.created_scripts == 2
    assert result.updated_scripts == 0

    assert result.total_statements == 2
    assert result.successful_statements == 2
    assert result.failed_statements == 0

    assert result.table_access_count == 4
    assert len(result.files) == 2

    # --------------------------------------------------------
    # 检查自动生成的脚本依赖统计
    # --------------------------------------------------------

    assert (
        result.deleted_dependency_count
        == 0
    )

    assert (
        result.total_dependency_count
        == 1
    )

    assert (
        result.confirmed_dependency_count
        == 1
    )

    assert (
        result.ambiguous_dependency_count
        == 0
    )

    assert (
        result.dependency_via_table_count
        == 1
    )

    # --------------------------------------------------------
    # 检查 SourceScript
    # --------------------------------------------------------

    scripts = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).all()

    assert len(scripts) == 2

    assert {
        script.relative_path
        for script in scripts
    } == {
        "hive/01_ods_to_dwd.sql",
        "hive/02_dwd_to_ads.sql",
    }

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
        "ads.order_summary",
    }

    # --------------------------------------------------------
    # 检查 ScriptTableAccess
    # --------------------------------------------------------

    script_ids = {
        script.id
        for script in scripts
    }

    accesses = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id.in_(
                script_ids
            )
        )
    ).all()

    assert len(accesses) == 4
    # --------------------------------------------------------
    # 检查 ScriptDependency
    # --------------------------------------------------------

    dependencies = db_session.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project.id
        )
    ).all()

    assert len(dependencies) == 1

    dependency = dependencies[0]

    assert (
            dependency.dependency_status
            == "confirmed"
    )

    assert dependency.reason is None

    assert (
            dependency.via_table.full_name
            == "dwd.orders"
    )

    assert (
            dependency.upstream_script
            .relative_path
            == "hive/01_ods_to_dwd.sql"
    )

    assert (
            dependency.downstream_script
            .relative_path
            == "hive/02_dwd_to_ads.sql"
    )


    # --------------------------------------------------------
    # 检查目录汇总
    # --------------------------------------------------------

    assert result.status == "success"
    assert result.total_files == 2
    assert result.success_files == 2
    assert result.failed_files == 0

    assert result.created_scripts == 2
    assert result.updated_scripts == 0

    assert result.total_statements == 2
    assert result.successful_statements == 2
    assert result.failed_statements == 0

    assert result.table_access_count == 4
    assert len(result.files) == 2
    # --------------------------------------------------------
    # 检查自动生成的脚本依赖
    # --------------------------------------------------------

    assert (
        result.deleted_dependency_count
        == 0
    )

    assert (
        result.total_dependency_count
        == 1
    )

    assert (
        result.confirmed_dependency_count
        == 1
    )

    assert (
        result.ambiguous_dependency_count
        == 0
    )

    assert (
        result.dependency_via_table_count
        == 1
    )
    # --------------------------------------------------------
    # 检查 SourceScript
    # --------------------------------------------------------

    scripts = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).all()

    assert len(scripts) == 2

    assert {
        script.relative_path
        for script in scripts
    } == {
        "hive/01_ods_to_dwd.sql",
        "hive/02_dwd_to_ads.sql",
    }

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
        "ads.order_summary",
    }

    # --------------------------------------------------------
    # 检查 ScriptTableAccess
    # --------------------------------------------------------

    script_ids = {
        script.id
        for script in scripts
    }

    accesses = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id.in_(
                script_ids
            )
        )
    ).all()

    assert len(accesses) == 4


# ============================================================
# 2. 测试部分 SQL 文件解析失败
# ============================================================

def test_directory_partial_result(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session,
        "partial_project",
    )

    sql_root = tmp_path / "sql_source"
    hive_directory = sql_root / "hive"

    hive_directory.mkdir(parents=True)

    good_file = (
        hive_directory
        / "good.sql"
    )

    good_file.write_text(
        (
            "INSERT INTO dwd.orders\n"
            "SELECT * FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    broken_file = (
        hive_directory
        / "broken.sql"
    )

    broken_file.write_text(
        "SELECT (1 +;",
        encoding="utf-8",
    )

    result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert result.status == "partial"

    assert result.total_files == 2
    assert result.success_files == 1
    assert result.failed_files == 1

    assert result.created_scripts == 2
    assert result.total_statements == 2

    assert result.successful_statements == 1
    assert result.failed_statements == 1

    # 只有正确文件生成两个表访问：
    #
    # READ ods.orders
    # WRITE dwd.orders
    assert result.table_access_count == 2

    scripts = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).all()

    assert len(scripts) == 2

    script_statuses = {
        script.relative_path:
        script.parse_status
        for script in scripts
    }

    assert script_statuses[
        "hive/good.sql"
    ] == "success"

    assert script_statuses[
        "hive/broken.sql"
    ] == "failed"
    # 当前只有 good.sql：
    #
    # READ  ods.orders
    # WRITE dwd.orders
    #
    # 没有其他脚本读取 dwd.orders，
    # 所以不会形成脚本依赖。
    assert (
        result.total_dependency_count
        == 0
    )

    assert (
        result.confirmed_dependency_count
        == 0
    )

    assert (
        result.ambiguous_dependency_count
        == 0
    )

# ============================================================
# 3. 测试空目录
# ============================================================

def test_empty_sql_directory(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session,
        "empty_directory_project",
    )

    sql_root = tmp_path / "empty_source"
    sql_root.mkdir()

    # 创建非 SQL 文件，确认会被忽略。
    readme_file = sql_root / "readme.txt"

    readme_file.write_text(
        "没有 SQL 文件",
        encoding="utf-8",
    )

    result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert result.status == "empty"
    assert result.total_files == 0
    assert result.success_files == 0
    assert result.failed_files == 0
    assert result.created_scripts == 0
    assert result.updated_scripts == 0
    assert result.table_access_count == 0
    assert result.files == ()
    assert (
        result.total_dependency_count
        == 0
    )

    assert (
        result.confirmed_dependency_count
        == 0
    )

    assert (
        result.ambiguous_dependency_count
        == 0
    )

# ============================================================
# 4. 测试重新导入目录
# ============================================================

def test_reingest_directory_updates_scripts(
    db_session,
    tmp_path,
):
    project = create_test_project(
        db_session,
        "reingest_directory_project",
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
            "SELECT * FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    first_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert first_result.created_scripts == 1
    assert first_result.updated_scripts == 0

    # --------------------------------------------------------
    # 修改同一路径文件
    # --------------------------------------------------------

    sql_file.write_text(
        (
            "INSERT INTO ads.order_summary\n"
            "SELECT * FROM dwd.orders;\n"
        ),
        encoding="utf-8",
    )

    second_result = ingest_sql_directory(
        db=db_session,
        project_id=project.id,
        root_directory=sql_root,
    )

    assert second_result.created_scripts == 0
    assert second_result.updated_scripts == 1

    scripts = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).all()

    # 同一路径仍然只有一条记录。
    assert len(scripts) == 1

    assert "ads.order_summary" in (
        scripts[0].source_code
    )

    current_accesses = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id
            == scripts[0].id
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


# ============================================================
# 5. 测试项目不存在
# ============================================================

def test_directory_ingestion_rejects_missing_project(
    db_session,
    tmp_path,
):
    """
    项目不存在时，目录导入必须明确拒绝。
    """

    sql_root = tmp_path / "sql_source"
    sql_root.mkdir()

    sql_file = sql_root / "orders.sql"

    sql_file.write_text(
        "SELECT * FROM ods.orders;",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="血缘项目不存在",
    ):
        ingest_sql_directory(
            db=db_session,
            project_id=-1,
            root_directory=sql_root,
        )
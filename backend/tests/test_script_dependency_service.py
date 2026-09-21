from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)
from app.services.script_dependency_service import (
    rebuild_script_dependencies,
)


# ============================================================
# 1. 创建测试项目
# ============================================================

def create_project(
    db_session,
    prefix: str,
) -> LineageProject:
    project = LineageProject(
        name=(
            f"{prefix}_{uuid4().hex[:8]}"
        ),
        description=(
            "script dependency pytest"
        ),
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建测试脚本
# ============================================================

def create_script(
    db_session,
    project_id: int,
    file_name: str,
) -> SourceScript:
    file_hash = sha256(
        file_name.encode("utf-8")
    ).hexdigest()

    script = SourceScript(
        project_id=project_id,
        file_name=file_name,
        relative_path=file_name,
        dialect="hive",
        file_hash=file_hash,
        source_code="SELECT 1",
        parse_status="success",
        parse_error=None,
    )

    db_session.add(script)
    db_session.flush()

    return script


# ============================================================
# 3. 创建测试数据表
# ============================================================

def create_table(
    db_session,
    project_id: int,
    full_name: str,
) -> DataTable:
    name_parts = full_name.split(".")

    if len(name_parts) == 2:
        schema_name = name_parts[0]
        table_name = name_parts[1]

    else:
        schema_name = None
        table_name = name_parts[-1]

    data_table = DataTable(
        project_id=project_id,
        schema_name=schema_name,
        table_name=table_name,
        full_name=full_name,
        table_kind="unknown",
    )

    db_session.add(data_table)
    db_session.flush()

    return data_table


# ============================================================
# 4. 创建 READ / WRITE 记录
# ============================================================

def create_access(
    db_session,
    script_id: int,
    table_id: int,
    access_type: str,
    statement_no: int = 1,
) -> ScriptTableAccess:
    access = ScriptTableAccess(
        script_id=script_id,
        table_id=table_id,
        access_type=access_type,
        statement_no=statement_no,
        line_start=1,
        line_end=1,
        evidence_sql="test SQL",
    )

    db_session.add(access)
    db_session.flush()

    return access


# ============================================================
# 5. 测试单一上游
# ============================================================

def test_build_confirmed_dependency(
    db_session,
):
    """
    A WRITE table
    B READ  table

    应生成：

        A -> B confirmed
    """

    project = create_project(
        db_session,
        "confirmed_project",
    )

    upstream_script = create_script(
        db_session,
        project.id,
        "upstream.sql",
    )

    downstream_script = create_script(
        db_session,
        project.id,
        "downstream.sql",
    )

    data_table = create_table(
        db_session,
        project.id,
        "dwd.orders",
    )

    create_access(
        db_session,
        upstream_script.id,
        data_table.id,
        "write",
    )

    create_access(
        db_session,
        downstream_script.id,
        data_table.id,
        "read",
    )

    result = rebuild_script_dependencies(
        db=db_session,
        project_id=project.id,
    )

    assert result.total_dependency_count == 1
    assert result.confirmed_dependency_count == 1
    assert result.ambiguous_dependency_count == 0
    assert result.via_table_count == 1

    dependencies = db_session.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project.id
        )
    ).all()

    assert len(dependencies) == 1

    dependency = dependencies[0]

    assert (
        dependency.upstream_script_id
        == upstream_script.id
    )

    assert (
        dependency.downstream_script_id
        == downstream_script.id
    )

    assert (
        dependency.via_table_id
        == data_table.id
    )

    assert (
        dependency.dependency_status
        == "confirmed"
    )

    assert dependency.reason is None


# ============================================================
# 6. 测试多个可能上游
# ============================================================

def test_build_ambiguous_dependencies(
    db_session,
):
    """
    A WRITE table
    B WRITE table
    C READ  table

    应生成：

        A -> C ambiguous
        B -> C ambiguous
    """

    project = create_project(
        db_session,
        "ambiguous_project",
    )

    script_a = create_script(
        db_session,
        project.id,
        "a.sql",
    )

    script_b = create_script(
        db_session,
        project.id,
        "b.sql",
    )

    script_c = create_script(
        db_session,
        project.id,
        "c.sql",
    )

    data_table = create_table(
        db_session,
        project.id,
        "dwd.orders",
    )

    create_access(
        db_session,
        script_a.id,
        data_table.id,
        "write",
    )

    create_access(
        db_session,
        script_b.id,
        data_table.id,
        "write",
    )

    create_access(
        db_session,
        script_c.id,
        data_table.id,
        "read",
    )

    result = rebuild_script_dependencies(
        db=db_session,
        project_id=project.id,
    )

    assert result.total_dependency_count == 2
    assert result.confirmed_dependency_count == 0
    assert result.ambiguous_dependency_count == 2

    dependencies = db_session.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project.id
        )
    ).all()

    assert len(dependencies) == 2

    assert {
        dependency.upstream_script_id
        for dependency in dependencies
    } == {
        script_a.id,
        script_b.id,
    }

    assert all(
        dependency.downstream_script_id
        == script_c.id
        for dependency in dependencies
    )

    assert all(
        dependency.dependency_status
        == "ambiguous"
        for dependency in dependencies
    )

    assert all(
        dependency.reason is not None
        for dependency in dependencies
    )


# ============================================================
# 7. 测试不生成自依赖
# ============================================================

def test_self_dependency_is_not_created(
    db_session,
):
    project = create_project(
        db_session,
        "self_dependency_project",
    )

    script = create_script(
        db_session,
        project.id,
        "self_read_write.sql",
    )

    data_table = create_table(
        db_session,
        project.id,
        "dwd.orders",
    )

    create_access(
        db_session,
        script.id,
        data_table.id,
        "write",
        statement_no=1,
    )

    create_access(
        db_session,
        script.id,
        data_table.id,
        "read",
        statement_no=2,
    )

    result = rebuild_script_dependencies(
        db=db_session,
        project_id=project.id,
    )

    assert result.total_dependency_count == 0
    assert result.via_table_count == 0

    dependencies = db_session.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project.id
        )
    ).all()

    assert dependencies == []


# ============================================================
# 8. 测试重建会删除失效依赖
# ============================================================

def test_rebuild_removes_stale_dependency(
    db_session,
):
    project = create_project(
        db_session,
        "rebuild_project",
    )

    upstream_script = create_script(
        db_session,
        project.id,
        "upstream.sql",
    )

    downstream_script = create_script(
        db_session,
        project.id,
        "downstream.sql",
    )

    data_table = create_table(
        db_session,
        project.id,
        "dwd.orders",
    )

    create_access(
        db_session,
        upstream_script.id,
        data_table.id,
        "write",
    )

    read_access = create_access(
        db_session,
        downstream_script.id,
        data_table.id,
        "read",
    )

    first_result = (
        rebuild_script_dependencies(
            db=db_session,
            project_id=project.id,
        )
    )

    assert (
        first_result.total_dependency_count
        == 1
    )

    # 模拟下游脚本修改后不再读取这张表。
    db_session.delete(read_access)
    db_session.flush()

    second_result = (
        rebuild_script_dependencies(
            db=db_session,
            project_id=project.id,
        )
    )

    assert (
        second_result.deleted_dependency_count
        == 1
    )

    assert (
        second_result.total_dependency_count
        == 0
    )

    dependencies = db_session.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project.id
        )
    ).all()

    assert dependencies == []


# ============================================================
# 9. 测试同一写入脚本不会重复生成依赖
# ============================================================

def test_duplicate_writer_accesses_create_one_dependency(
    db_session,
):
    """
    同一个脚本在两条语句中写同一张表，
    它仍然只是一个上游脚本。
    """

    project = create_project(
        db_session,
        "duplicate_writer_project",
    )

    upstream_script = create_script(
        db_session,
        project.id,
        "upstream.sql",
    )

    downstream_script = create_script(
        db_session,
        project.id,
        "downstream.sql",
    )

    data_table = create_table(
        db_session,
        project.id,
        "dwd.orders",
    )

    create_access(
        db_session,
        upstream_script.id,
        data_table.id,
        "write",
        statement_no=1,
    )

    create_access(
        db_session,
        upstream_script.id,
        data_table.id,
        "write",
        statement_no=2,
    )

    create_access(
        db_session,
        downstream_script.id,
        data_table.id,
        "read",
        statement_no=1,
    )

    result = rebuild_script_dependencies(
        db=db_session,
        project_id=project.id,
    )

    assert result.total_dependency_count == 1
    assert result.confirmed_dependency_count == 1

    dependencies = db_session.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project.id
        )
    ).all()

    assert len(dependencies) == 1
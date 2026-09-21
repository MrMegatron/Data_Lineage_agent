from hashlib import sha256
from uuid import uuid4
import pytest
from app.models import (
    DataColumn,
    DataTable,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)
from app.services.catalog_query_service import (
    list_project_scripts,
    list_project_tables,
    list_projects,
)


def create_test_project(
    db_session,
    name: str,
) -> LineageProject:
    project = LineageProject(
        name=name,
        description="catalog pytest",
    )

    db_session.add(project)
    db_session.flush()

    return project


def create_test_script(
    db_session,
    project_id: int,
    file_name: str,
) -> SourceScript:
    script = SourceScript(
        project_id=project_id,
        file_name=file_name,
        relative_path=file_name,
        dialect="hive",
        file_hash=sha256(
            file_name.encode("utf-8")
        ).hexdigest(),
        source_code="SELECT 1",
        parse_status="success",
        parse_error=None,
    )

    db_session.add(script)
    db_session.flush()

    return script


# ============================================================
# 1. 测试项目列表和统计
# ============================================================

def test_list_projects_with_counts(
    db_session,
):
    unique_prefix = (
        "catalog_"
        f"{uuid4().hex[:10]}"
    )

    project_a = create_test_project(
        db_session,
        f"{unique_prefix}_a",
    )

    project_b = create_test_project(
        db_session,
        f"{unique_prefix}_b",
    )

    script_a = create_test_script(
        db_session,
        project_a.id,
        "a.sql",
    )

    script_b = create_test_script(
        db_session,
        project_a.id,
        "b.sql",
    )

    data_table = DataTable(
        project_id=project_a.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="unknown",
    )

    db_session.add(data_table)
    db_session.flush()

    dependency = ScriptDependency(
        project_id=project_a.id,
        upstream_script_id=script_a.id,
        downstream_script_id=script_b.id,
        via_table_id=data_table.id,
        dependency_status="confirmed",
        reason=None,
    )

    db_session.add(dependency)
    db_session.flush()

    result = list_projects(
        db=db_session,
        page=1,
        page_size=20,
        keyword=unique_prefix,
    )

    assert result.total == 2
    assert len(result.items) == 2

    items_by_id = {
        item.project_id: item
        for item in result.items
    }

    assert (
        items_by_id[project_a.id]
        .script_count
        == 2
    )

    assert (
        items_by_id[project_a.id]
        .table_count
        == 1
    )

    assert (
        items_by_id[project_a.id]
        .dependency_count
        == 1
    )

    assert (
        items_by_id[project_b.id]
        .script_count
        == 0
    )

    assert (
        items_by_id[project_b.id]
        .table_count
        == 0
    )

    assert (
        items_by_id[project_b.id]
        .dependency_count
        == 0
    )


# ============================================================
# 2. 测试分页
# ============================================================

def test_list_projects_pagination(
    db_session,
):
    unique_prefix = (
        "page_"
        f"{uuid4().hex[:10]}"
    )

    create_test_project(
        db_session,
        f"{unique_prefix}_a",
    )

    create_test_project(
        db_session,
        f"{unique_prefix}_b",
    )

    first_page = list_projects(
        db=db_session,
        page=1,
        page_size=1,
        keyword=unique_prefix,
    )

    second_page = list_projects(
        db=db_session,
        page=2,
        page_size=1,
        keyword=unique_prefix,
    )

    assert first_page.total == 2
    assert second_page.total == 2

    assert len(first_page.items) == 1
    assert len(second_page.items) == 1

    assert (
        first_page.items[0].project_id
        != second_page.items[0].project_id
    )


# ============================================================
# 3. 测试搜索不到项目
# ============================================================

def test_list_projects_no_match(
    db_session,
):
    result = list_projects(
        db=db_session,
        page=1,
        page_size=20,
        keyword=(
            "not_exists_"
            f"{uuid4().hex}"
        ),
    )

    assert result.total == 0
    assert result.items == ()

# ============================================================
# 4. 测试项目脚本列表和统计
# ============================================================

def test_list_project_scripts_with_counts(
    db_session,
):
    unique_prefix = (
        "script_list_"
        f"{uuid4().hex[:10]}"
    )

    project = create_test_project(
        db_session,
        unique_prefix,
    )

    script_a = create_test_script(
        db_session,
        project.id,
        "01_upstream.sql",
    )

    script_b = create_test_script(
        db_session,
        project.id,
        "02_downstream.sql",
    )

    input_table = DataTable(
        project_id=project.id,
        schema_name="ods",
        table_name="orders",
        full_name="ods.orders",
        table_kind="unknown",
    )

    middle_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="unknown",
    )

    db_session.add_all([
        input_table,
        middle_table,
    ])

    db_session.flush()

    script_a_read = ScriptTableAccess(
        script_id=script_a.id,
        table_id=input_table.id,
        access_type="read",
        statement_no=1,
        evidence_sql="FROM ods.orders",
    )

    script_a_write = ScriptTableAccess(
        script_id=script_a.id,
        table_id=middle_table.id,
        access_type="write",
        statement_no=1,
        evidence_sql="INSERT INTO dwd.orders",
    )

    script_b_read = ScriptTableAccess(
        script_id=script_b.id,
        table_id=middle_table.id,
        access_type="read",
        statement_no=1,
        evidence_sql="FROM dwd.orders",
    )

    db_session.add_all([
        script_a_read,
        script_a_write,
        script_b_read,
    ])

    db_session.flush()

    dependency = ScriptDependency(
        project_id=project.id,
        upstream_script_id=script_a.id,
        downstream_script_id=script_b.id,
        via_table_id=middle_table.id,
        dependency_status="confirmed",
        reason=None,
    )

    db_session.add(dependency)
    db_session.flush()

    result = list_project_scripts(
        db=db_session,
        project_id=project.id,
        page=1,
        page_size=20,
    )

    assert result.project_id == project.id
    assert result.total == 2
    assert len(result.items) == 2

    items_by_id = {
        item.script_id: item
        for item in result.items
    }

    upstream_item = items_by_id[
        script_a.id
    ]

    assert (
        upstream_item.read_table_count
        == 1
    )

    assert (
        upstream_item.write_table_count
        == 1
    )

    assert (
        upstream_item
        .upstream_dependency_count
        == 0
    )

    assert (
        upstream_item
        .downstream_dependency_count
        == 1
    )

    downstream_item = items_by_id[
        script_b.id
    ]

    assert (
        downstream_item.read_table_count
        == 1
    )

    assert (
        downstream_item.write_table_count
        == 0
    )

    assert (
        downstream_item
        .upstream_dependency_count
        == 1
    )

    assert (
        downstream_item
        .downstream_dependency_count
        == 0
    )


# ============================================================
# 5. 测试解析状态过滤
# ============================================================

def test_list_project_scripts_status_filter(
    db_session,
):
    project = create_test_project(
        db_session,
        (
            "status_filter_"
            f"{uuid4().hex[:8]}"
        ),
    )

    success_script = create_test_script(
        db_session,
        project.id,
        "success.sql",
    )

    failed_script = create_test_script(
        db_session,
        project.id,
        "failed.sql",
    )

    failed_script.parse_status = "failed"
    failed_script.parse_error = (
        "test parse error"
    )

    db_session.flush()

    result = list_project_scripts(
        db=db_session,
        project_id=project.id,
        page=1,
        page_size=20,
        parse_status="failed",
    )

    assert result.total == 1
    assert len(result.items) == 1

    assert (
        result.items[0].script_id
        == failed_script.id
    )

    assert (
        result.items[0].parse_status
        == "failed"
    )

    assert (
        result.items[0].parse_error
        == "test parse error"
    )

    assert (
        result.items[0].script_id
        != success_script.id
    )


# ============================================================
# 6. 测试不存在的项目
# ============================================================

def test_list_scripts_missing_project(
    db_session,
):
    import pytest

    with pytest.raises(
        ValueError,
        match="血缘项目不存在",
    ):
        list_project_scripts(
            db=db_session,
            project_id=-1,
            page=1,
            page_size=20,
        )


# ============================================================
# 7. 测试数据表列表和统计
# ============================================================

def test_list_project_tables_with_counts(
    db_session,
):
    project = create_test_project(
        db_session,
        (
            "table_list_"
            f"{uuid4().hex[:8]}"
        ),
    )

    writer_script = create_test_script(
        db_session,
        project.id,
        "writer.sql",
    )

    reader_script = create_test_script(
        db_session,
        project.id,
        "reader.sql",
    )

    data_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="unknown",
    )

    db_session.add(data_table)
    db_session.flush()

    first_column = DataColumn(
        table_id=data_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="string",
    )

    second_column = DataColumn(
        table_id=data_table.id,
        column_name="amount",
        ordinal_position=2,
        data_type="decimal",
    )

    db_session.add_all([
        first_column,
        second_column,
    ])

    db_session.flush()

    write_access = ScriptTableAccess(
        script_id=writer_script.id,
        table_id=data_table.id,
        access_type="write",
        statement_no=1,
        evidence_sql=(
            "INSERT INTO dwd.orders"
        ),
    )

    read_access = ScriptTableAccess(
        script_id=reader_script.id,
        table_id=data_table.id,
        access_type="read",
        statement_no=1,
        evidence_sql=(
            "FROM dwd.orders"
        ),
    )

    db_session.add_all([
        write_access,
        read_access,
    ])

    db_session.flush()

    dependency = ScriptDependency(
        project_id=project.id,
        upstream_script_id=(
            writer_script.id
        ),
        downstream_script_id=(
            reader_script.id
        ),
        via_table_id=data_table.id,
        dependency_status="confirmed",
        reason=None,
    )

    db_session.add(dependency)
    db_session.flush()

    result = list_project_tables(
        db=db_session,
        project_id=project.id,
        page=1,
        page_size=20,
    )

    assert result.project_id == project.id
    assert result.total == 1
    assert len(result.items) == 1

    item = result.items[0]

    assert item.table_id == data_table.id
    assert item.full_name == "dwd.orders"

    assert item.column_count == 2

    assert (
        item.writer_script_count
        == 1
    )

    assert (
        item.reader_script_count
        == 1
    )

    assert item.dependency_count == 1


# ============================================================
# 8. 测试表名搜索和类型过滤
# ============================================================

def test_list_project_tables_filter(
    db_session,
):
    project = create_test_project(
        db_session,
        (
            "table_filter_"
            f"{uuid4().hex[:8]}"
        ),
    )

    physical_table = DataTable(
        project_id=project.id,
        schema_name="ods",
        table_name="orders",
        full_name="ods.orders",
        table_kind="physical",
    )

    view_table = DataTable(
        project_id=project.id,
        schema_name="ads",
        table_name="order_summary",
        full_name="ads.order_summary",
        table_kind="view",
    )

    db_session.add_all([
        physical_table,
        view_table,
    ])

    db_session.flush()

    result = list_project_tables(
        db=db_session,
        project_id=project.id,
        page=1,
        page_size=20,
        keyword="summary",
        table_kind="view",
    )

    assert result.total == 1
    assert len(result.items) == 1

    assert (
        result.items[0].table_id
        == view_table.id
    )

    assert (
        result.items[0].full_name
        == "ads.order_summary"
    )

    assert (
        result.items[0].table_kind
        == "view"
    )

    assert (
        result.items[0].table_id
        != physical_table.id
    )


# ============================================================
# 9. 测试不存在的项目
# ============================================================

def test_list_tables_missing_project(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="血缘项目不存在",
    ):
        list_project_tables(
            db=db_session,
            project_id=-1,
            page=1,
            page_size=20,
        )
from hashlib import sha256
from uuid import uuid4

import pytest

from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)
from app.services.script_detail_service import (
    get_script_detail,
)


def create_project(
    db_session,
    prefix: str,
) -> LineageProject:
    project = LineageProject(
        name=(
            f"{prefix}_{uuid4().hex[:8]}"
        ),
        description="script detail pytest",
    )

    db_session.add(project)
    db_session.flush()

    return project


def create_script(
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
        source_code=(
            f"-- {file_name}\n"
            "SELECT 1"
        ),
        parse_status="success",
        parse_error=None,
    )

    db_session.add(script)
    db_session.flush()

    return script


def create_table(
    db_session,
    project_id: int,
    full_name: str,
) -> DataTable:
    schema_name, table_name = (
        full_name.split(".", maxsplit=1)
    )

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


def test_get_script_detail(
    db_session,
):
    project = create_project(
        db_session,
        "detail_project",
    )

    upstream = create_script(
        db_session,
        project.id,
        "upstream.sql",
    )

    current = create_script(
        db_session,
        project.id,
        "current.sql",
    )

    downstream = create_script(
        db_session,
        project.id,
        "downstream.sql",
    )

    input_table = create_table(
        db_session,
        project.id,
        "dwd.orders",
    )

    output_table = create_table(
        db_session,
        project.id,
        "ads.order_summary",
    )

    read_access = ScriptTableAccess(
        script_id=current.id,
        table_id=input_table.id,
        access_type="read",
        statement_no=1,
        line_start=2,
        line_end=3,
        evidence_sql=(
            "SELECT * FROM dwd.orders"
        ),
    )

    write_access = ScriptTableAccess(
        script_id=current.id,
        table_id=output_table.id,
        access_type="write",
        statement_no=1,
        line_start=1,
        line_end=3,
        evidence_sql=(
            "INSERT INTO ads.order_summary"
        ),
    )

    db_session.add_all([
        read_access,
        write_access,
    ])

    db_session.flush()

    upstream_dependency = ScriptDependency(
        project_id=project.id,
        upstream_script_id=upstream.id,
        downstream_script_id=current.id,
        via_table_id=input_table.id,
        dependency_status="confirmed",
        reason=None,
    )

    downstream_dependency = ScriptDependency(
        project_id=project.id,
        upstream_script_id=current.id,
        downstream_script_id=downstream.id,
        via_table_id=output_table.id,
        dependency_status="confirmed",
        reason=None,
    )

    db_session.add_all([
        upstream_dependency,
        downstream_dependency,
    ])

    db_session.flush()

    result = get_script_detail(
        db=db_session,
        project_id=project.id,
        script_id=current.id,
    )

    assert result.script_id == current.id
    assert result.project_id == project.id
    assert result.file_name == "current.sql"

    assert result.table_access_count == 2
    assert result.read_table_count == 1
    assert result.write_table_count == 1

    assert result.upstream_count == 1
    assert result.downstream_count == 1

    assert {
        (
            access.access_type,
            access.full_name,
        )
        for access in result.table_accesses
    } == {
        ("read", "dwd.orders"),
        ("write", "ads.order_summary"),
    }

    assert (
        result.upstream_dependencies[0]
        .related_script_id
        == upstream.id
    )

    assert (
        result.upstream_dependencies[0]
        .via_table_full_name
        == "dwd.orders"
    )

    assert (
        result.downstream_dependencies[0]
        .related_script_id
        == downstream.id
    )

    assert (
        result.downstream_dependencies[0]
        .via_table_full_name
        == "ads.order_summary"
    )


def test_script_not_in_project(
    db_session,
):
    project_a = create_project(
        db_session,
        "project_a",
    )

    project_b = create_project(
        db_session,
        "project_b",
    )

    script_b = create_script(
        db_session,
        project_b.id,
        "other.sql",
    )

    with pytest.raises(
        ValueError,
        match="脚本不存在或不属于当前项目",
    ):
        get_script_detail(
            db=db_session,
            project_id=project_a.id,
            script_id=script_b.id,
        )


def test_script_detail_missing_project(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="血缘项目不存在",
    ):
        get_script_detail(
            db=db_session,
            project_id=-1,
            script_id=-1,
        )
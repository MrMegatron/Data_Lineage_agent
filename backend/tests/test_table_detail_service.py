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
from app.services.table_detail_service import (
    get_table_detail,
)


def create_project(
    db_session,
    prefix: str,
) -> LineageProject:
    project = LineageProject(
        name=(
            f"{prefix}_{uuid4().hex[:8]}"
        ),
        description="table detail pytest",
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
        source_code="SELECT 1",
        parse_status="success",
        parse_error=None,
    )

    db_session.add(script)
    db_session.flush()

    return script


def test_get_table_detail(
    db_session,
):
    project = create_project(
        db_session,
        "table_detail_project",
    )

    writer = create_script(
        db_session,
        project.id,
        "writer.sql",
    )

    reader = create_script(
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

    order_id = DataColumn(
        table_id=data_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="string",
    )

    amount = DataColumn(
        table_id=data_table.id,
        column_name="amount",
        ordinal_position=2,
        data_type="decimal",
    )

    db_session.add_all([
        order_id,
        amount,
    ])

    db_session.flush()

    write_access = ScriptTableAccess(
        script_id=writer.id,
        table_id=data_table.id,
        access_type="write",
        statement_no=1,
        line_start=1,
        line_end=3,
        evidence_sql=(
            "INSERT INTO dwd.orders"
        ),
    )

    read_access = ScriptTableAccess(
        script_id=reader.id,
        table_id=data_table.id,
        access_type="read",
        statement_no=1,
        line_start=1,
        line_end=3,
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
        upstream_script_id=writer.id,
        downstream_script_id=reader.id,
        via_table_id=data_table.id,
        dependency_status="confirmed",
        reason=None,
    )

    db_session.add(dependency)
    db_session.flush()

    result = get_table_detail(
        db=db_session,
        project_id=project.id,
        table_id=data_table.id,
    )

    assert result.table_id == data_table.id
    assert result.full_name == "dwd.orders"

    assert result.column_count == 2

    assert [
        column.column_name
        for column in result.columns
    ] == [
        "order_id",
        "amount",
    ]

    assert result.access_count == 2
    assert result.writer_script_count == 1
    assert result.reader_script_count == 1

    assert len(result.writers) == 1
    assert len(result.readers) == 1

    assert (
        result.writers[0].script_id
        == writer.id
    )

    assert (
        result.readers[0].script_id
        == reader.id
    )

    assert result.dependency_count == 1

    assert (
        result.confirmed_dependency_count
        == 1
    )

    assert (
        result.ambiguous_dependency_count
        == 0
    )

    assert (
        result.dependencies[0]
        .upstream_script_id
        == writer.id
    )

    assert (
        result.dependencies[0]
        .downstream_script_id
        == reader.id
    )


def test_table_not_in_project(
    db_session,
):
    project_a = create_project(
        db_session,
        "table_project_a",
    )

    project_b = create_project(
        db_session,
        "table_project_b",
    )

    data_table = DataTable(
        project_id=project_b.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="unknown",
    )

    db_session.add(data_table)
    db_session.flush()

    with pytest.raises(
        ValueError,
        match="数据表不存在或不属于当前项目",
    ):
        get_table_detail(
            db=db_session,
            project_id=project_a.id,
            table_id=data_table.id,
        )


def test_table_detail_missing_project(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="血缘项目不存在",
    ):
        get_table_detail(
            db=db_session,
            project_id=-1,
            table_id=-1,
        )
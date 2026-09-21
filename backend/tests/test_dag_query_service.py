from hashlib import sha256
from uuid import uuid4

import pytest

from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    SourceScript,
)
from app.services.dag_query_service import (
    get_project_script_dag,
)


def create_project(
    db_session,
    prefix: str,
) -> LineageProject:
    project = LineageProject(
        name=(
            f"{prefix}_{uuid4().hex[:8]}"
        ),
        description="DAG query pytest",
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


def create_dependency(
    db_session,
    project_id: int,
    upstream_script_id: int,
    downstream_script_id: int,
    table_id: int,
    dependency_status: str,
    reason: str | None = None,
) -> ScriptDependency:
    dependency = ScriptDependency(
        project_id=project_id,
        upstream_script_id=(
            upstream_script_id
        ),
        downstream_script_id=(
            downstream_script_id
        ),
        via_table_id=table_id,
        dependency_status=(
            dependency_status
        ),
        reason=reason,
    )

    db_session.add(dependency)
    db_session.flush()

    return dependency


# ============================================================
# 1. 测试普通线性 DAG
# ============================================================

def test_get_confirmed_dag(
    db_session,
):
    project = create_project(
        db_session,
        "confirmed_dag",
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

    isolated_script = create_script(
        db_session,
        project.id,
        "isolated.sql",
    )

    data_table = create_table(
        db_session,
        project.id,
        "dwd.orders",
    )

    dependency = create_dependency(
        db_session,
        project.id,
        script_a.id,
        script_b.id,
        data_table.id,
        "confirmed",
    )

    result = get_project_script_dag(
        db=db_session,
        project_id=project.id,
    )

    assert result.project_id == project.id
    assert result.node_count == 3
    assert result.edge_count == 1

    assert result.confirmed_edge_count == 1
    assert result.ambiguous_edge_count == 0

    assert len(result.nodes) == 3
    assert len(result.edges) == 1

    edge = result.edges[0]

    assert (
        edge.dependency_id
        == dependency.id
    )

    assert (
        edge.upstream_script_id
        == script_a.id
    )

    assert (
        edge.downstream_script_id
        == script_b.id
    )

    assert (
        edge.via_table_full_name
        == "dwd.orders"
    )

    nodes_by_id = {
        node.script_id: node
        for node in result.nodes
    }

    assert (
        nodes_by_id[script_a.id]
        .in_degree
        == 0
    )

    assert (
        nodes_by_id[script_a.id]
        .out_degree
        == 1
    )

    assert (
        nodes_by_id[script_b.id]
        .in_degree
        == 1
    )

    assert (
        nodes_by_id[script_b.id]
        .out_degree
        == 0
    )

    assert (
        nodes_by_id[isolated_script.id]
        .in_degree
        == 0
    )

    assert (
        nodes_by_id[isolated_script.id]
        .out_degree
        == 0
    )


# ============================================================
# 2. 测试 ambiguous DAG
# ============================================================

def test_get_ambiguous_dag(
    db_session,
):
    project = create_project(
        db_session,
        "ambiguous_dag",
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

    reason = (
        "表 dwd.orders 存在 2 个"
        "可能的上游写入脚本"
    )

    create_dependency(
        db_session,
        project.id,
        script_a.id,
        script_c.id,
        data_table.id,
        "ambiguous",
        reason,
    )

    create_dependency(
        db_session,
        project.id,
        script_b.id,
        script_c.id,
        data_table.id,
        "ambiguous",
        reason,
    )

    result = get_project_script_dag(
        db=db_session,
        project_id=project.id,
    )

    assert result.node_count == 3
    assert result.edge_count == 2

    assert result.confirmed_edge_count == 0
    assert result.ambiguous_edge_count == 2

    assert all(
        edge.dependency_status
        == "ambiguous"
        for edge in result.edges
    )

    assert all(
        edge.reason == reason
        for edge in result.edges
    )


# ============================================================
# 3. 测试空项目
# ============================================================

def test_get_empty_project_dag(
    db_session,
):
    project = create_project(
        db_session,
        "empty_dag",
    )

    result = get_project_script_dag(
        db=db_session,
        project_id=project.id,
    )

    assert result.node_count == 0
    assert result.edge_count == 0
    assert result.nodes == ()
    assert result.edges == ()


# ============================================================
# 4. 测试项目不存在
# ============================================================

def test_get_missing_project_dag(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="血缘项目不存在",
    ):
        get_project_script_dag(
            db=db_session,
            project_id=-1,
        )
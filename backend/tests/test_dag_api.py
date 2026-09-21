from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    SourceScript,
)


@pytest.fixture
def dag_api_client(
    db_session,
):
    """
    使用测试 Session 替换 FastAPI 数据库依赖。
    """

    def override_get_db():
        yield db_session

    app.dependency_overrides[
        get_db
    ] = override_get_db

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def create_api_project(
    db_session,
) -> LineageProject:
    project = LineageProject(
        name=(
            "dag_api_"
            f"{uuid4().hex[:8]}"
        ),
        description="DAG API pytest",
    )

    db_session.add(project)
    db_session.flush()

    return project


def create_api_script(
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
# 1. 测试 DAG API
# ============================================================

def test_get_project_dag_api(
    dag_api_client,
    db_session,
):
    project = create_api_project(
        db_session
    )

    upstream = create_api_script(
        db_session,
        project.id,
        "upstream.sql",
    )

    downstream = create_api_script(
        db_session,
        project.id,
        "downstream.sql",
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

    dependency = ScriptDependency(
        project_id=project.id,
        upstream_script_id=upstream.id,
        downstream_script_id=downstream.id,
        via_table_id=data_table.id,
        dependency_status="confirmed",
        reason=None,
    )

    db_session.add(dependency)
    db_session.flush()

    response = dag_api_client.get(
        f"/api/projects/{project.id}/dag"
    )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["project_id"]
        == project.id
    )

    assert response_data["node_count"] == 2
    assert response_data["edge_count"] == 1

    assert (
        response_data[
            "confirmed_edge_count"
        ]
        == 1
    )

    assert (
        response_data[
            "ambiguous_edge_count"
        ]
        == 0
    )

    assert len(response_data["nodes"]) == 2
    assert len(response_data["edges"]) == 1

    edge = response_data["edges"][0]

    assert (
        edge["upstream_script_id"]
        == upstream.id
    )

    assert (
        edge["downstream_script_id"]
        == downstream.id
    )

    assert (
        edge["via_table_full_name"]
        == "dwd.orders"
    )

    assert (
        edge["dependency_status"]
        == "confirmed"
    )


# ============================================================
# 2. 测试不存在的项目
# ============================================================

def test_get_missing_project_dag_api(
    dag_api_client,
):
    response = dag_api_client.get(
        "/api/projects/-1/dag"
    )

    assert response.status_code == 404

    assert (
        "血缘项目不存在"
        in response.json()["detail"]
    )
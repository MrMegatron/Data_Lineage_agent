from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    DataTable,
    LineageProject,
)


@pytest.fixture
def table_detail_client(
    db_session,
):
    def override_get_db():
        yield db_session

    app.dependency_overrides[
        get_db
    ] = override_get_db

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def test_get_table_detail_api(
    table_detail_client,
    db_session,
):
    project = LineageProject(
        name=(
            "table_api_"
            f"{uuid4().hex[:8]}"
        ),
        description="table detail API",
    )

    db_session.add(project)
    db_session.flush()

    data_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="unknown",
    )

    db_session.add(data_table)
    db_session.flush()

    response = table_detail_client.get(
        (
            f"/api/projects/{project.id}"
            f"/tables/{data_table.id}"
        )
    )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["project_id"]
        == project.id
    )

    assert (
        response_data["table_id"]
        == data_table.id
    )

    assert (
        response_data["full_name"]
        == "dwd.orders"
    )

    assert response_data["column_count"] == 0
    assert response_data["access_count"] == 0

    assert (
        response_data["writer_script_count"]
        == 0
    )

    assert (
        response_data["reader_script_count"]
        == 0
    )

    assert (
        response_data["dependency_count"]
        == 0
    )


def test_get_missing_table_api(
    table_detail_client,
    db_session,
):
    project = LineageProject(
        name=(
            "missing_table_"
            f"{uuid4().hex[:8]}"
        ),
    )

    db_session.add(project)
    db_session.flush()

    response = table_detail_client.get(
        (
            f"/api/projects/{project.id}"
            "/tables/-1"
        )
    )

    assert response.status_code == 404

    assert (
        "数据表不存在或不属于当前项目"
        in response.json()["detail"]
    )
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    LineageProject,
    SourceScript,
)


@pytest.fixture
def script_detail_client(
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


def test_get_script_detail_api(
    script_detail_client,
    db_session,
):
    project = LineageProject(
        name=(
            "detail_api_"
            f"{uuid4().hex[:8]}"
        ),
        description="script detail API",
    )

    db_session.add(project)
    db_session.flush()

    script = SourceScript(
        project_id=project.id,
        file_name="orders.sql",
        relative_path="orders.sql",
        dialect="hive",
        file_hash=sha256(
            b"orders.sql"
        ).hexdigest(),
        source_code=(
            "SELECT * FROM ods.orders"
        ),
        parse_status="success",
        parse_error=None,
    )

    db_session.add(script)
    db_session.flush()

    response = script_detail_client.get(
        (
            f"/api/projects/{project.id}"
            f"/scripts/{script.id}"
        )
    )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["project_id"]
        == project.id
    )

    assert (
        response_data["script_id"]
        == script.id
    )

    assert (
        response_data["file_name"]
        == "orders.sql"
    )

    assert (
        response_data["source_code"]
        == "SELECT * FROM ods.orders"
    )

    assert (
        response_data["table_access_count"]
        == 0
    )

    assert (
        response_data["upstream_count"]
        == 0
    )

    assert (
        response_data["downstream_count"]
        == 0
    )


def test_get_missing_script_api(
    script_detail_client,
    db_session,
):
    project = LineageProject(
        name=(
            "missing_script_"
            f"{uuid4().hex[:8]}"
        ),
    )

    db_session.add(project)
    db_session.flush()

    response = script_detail_client.get(
        (
            f"/api/projects/{project.id}"
            "/scripts/-1"
        )
    )

    assert response.status_code == 404

    assert (
        "脚本不存在或不属于当前项目"
        in response.json()["detail"]
    )
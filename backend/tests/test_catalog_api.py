from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    DataTable,
    LineageProject,
    SourceScript,
)


@pytest.fixture
def catalog_client(
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


# ============================================================
# 1. 测试项目列表 API
# ============================================================

def test_list_projects_api(
    catalog_client,
    db_session,
):
    unique_name = (
        "catalog_api_"
        f"{uuid4().hex[:10]}"
    )

    project = LineageProject(
        name=unique_name,
        description="catalog API pytest",
    )

    db_session.add(project)
    db_session.flush()

    response = catalog_client.get(
        "/api/projects",
        params={
            "page": 1,
            "page_size": 20,
            "keyword": unique_name,
        },
    )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["page"] == 1
    assert response_data["page_size"] == 20
    assert response_data["total"] == 1

    assert len(
        response_data["items"]
    ) == 1

    item = response_data["items"][0]

    assert (
        item["project_id"]
        == project.id
    )

    assert item["name"] == unique_name
    assert item["script_count"] == 0
    assert item["table_count"] == 0
    assert item["dependency_count"] == 0


# ============================================================
# 2. 测试分页参数校验
# ============================================================

def test_project_list_page_size_validation(
    catalog_client,
):
    response = catalog_client.get(
        "/api/projects",
        params={
            "page": 1,
            "page_size": 101,
        },
    )

    assert response.status_code == 422

# ============================================================
# 3. 测试项目脚本列表 API
# ============================================================

def test_list_project_scripts_api(
    catalog_client,
    db_session,
):
    project = LineageProject(
        name=(
            "script_api_"
            f"{uuid4().hex[:10]}"
        ),
        description="script list API",
    )

    db_session.add(project)
    db_session.flush()

    script = SourceScript(
        project_id=project.id,
        file_name="orders.sql",
        relative_path=(
            "hive/orders.sql"
        ),
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

    response = catalog_client.get(
        (
            f"/api/projects/{project.id}"
            "/scripts"
        ),
        params={
            "page": 1,
            "page_size": 20,
            "keyword": "orders",
            "parse_status": "success",
        },
    )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["project_id"]
        == project.id
    )

    assert response_data["total"] == 1
    assert len(response_data["items"]) == 1

    item = response_data["items"][0]

    assert item["script_id"] == script.id
    assert item["file_name"] == "orders.sql"

    assert (
        item["relative_path"]
        == "hive/orders.sql"
    )

    assert item["dialect"] == "hive"

    assert (
        item["parse_status"]
        == "success"
    )

    assert item["read_table_count"] == 0
    assert item["write_table_count"] == 0

    assert (
        item["upstream_dependency_count"]
        == 0
    )

    assert (
        item["downstream_dependency_count"]
        == 0
    )


# ============================================================
# 4. 测试不存在项目的脚本列表
# ============================================================

def test_list_project_scripts_missing_project_api(
    catalog_client,
):
    response = catalog_client.get(
        "/api/projects/-1/scripts"
    )

    assert response.status_code == 404

    assert (
        "血缘项目不存在"
        in response.json()["detail"]
    )

# ============================================================
# 5. 测试项目数据表列表 API
# ============================================================

def test_list_project_tables_api(
    catalog_client,
    db_session,
):
    project = LineageProject(
        name=(
            "table_api_"
            f"{uuid4().hex[:10]}"
        ),
        description="table list API",
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

    response = catalog_client.get(
        (
            f"/api/projects/{project.id}"
            "/tables"
        ),
        params={
            "page": 1,
            "page_size": 20,
            "keyword": "orders",
            "table_kind": "unknown",
        },
    )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["project_id"]
        == project.id
    )

    assert response_data["total"] == 1
    assert len(response_data["items"]) == 1

    item = response_data["items"][0]

    assert (
        item["table_id"]
        == data_table.id
    )

    assert (
        item["full_name"]
        == "dwd.orders"
    )

    assert (
        item["table_kind"]
        == "unknown"
    )

    assert item["column_count"] == 0

    assert (
        item["writer_script_count"]
        == 0
    )

    assert (
        item["reader_script_count"]
        == 0
    )

    assert item["dependency_count"] == 0


# ============================================================
# 6. 测试不存在项目的数据表列表
# ============================================================

def test_list_project_tables_missing_project_api(
    catalog_client,
):
    response = catalog_client.get(
        "/api/projects/-1/tables"
    )

    assert response.status_code == 404

    assert (
        "血缘项目不存在"
        in response.json()["detail"]
    )
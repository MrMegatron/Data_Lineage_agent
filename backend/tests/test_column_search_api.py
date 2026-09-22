from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageProject,
    SourceScript,
)


# ============================================================
# 1. 创建字段搜索API测试数据
# ============================================================

def create_column_search_api_data(
    db_session,
):
    """
    创建：

        ods.orders.order_id
                ↓
        dwd.orders.order_id
    """

    unique_suffix = uuid4().hex[:8]

    project = LineageProject(
        name=(
            "column_search_api_"
            f"{unique_suffix}"
        )
    )

    db_session.add(project)
    db_session.flush()

    source_table = DataTable(
        project_id=project.id,
        schema_name="ods",
        table_name="orders",
        full_name="ods.orders",
        table_kind="physical",
    )

    target_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="physical",
    )

    db_session.add_all(
        [
            source_table,
            target_table,
        ]
    )

    db_session.flush()

    source_column = DataColumn(
        table_id=source_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    target_column = DataColumn(
        table_id=target_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    db_session.add_all(
        [
            source_column,
            target_column,
        ]
    )

    db_session.flush()

    script = SourceScript(
        project_id=project.id,
        file_name="ods_to_dwd.sql",
        relative_path=(
            f"tests/{unique_suffix}/"
            "ods_to_dwd.sql"
        ),
        dialect="hive",
        file_hash="9" * 64,
        source_code=(
            "INSERT INTO dwd.orders "
            "(order_id) "
            "SELECT order_id "
            "FROM ods.orders"
        ),
        parse_status="success",
    )

    db_session.add(script)
    db_session.flush()

    lineage = ColumnLineage(
        project_id=project.id,
        script_id=script.id,
        source_column_id=source_column.id,
        target_column_id=target_column.id,
        relation_type="direct",
        resolution_status="confirmed",
        expression_text="order_id",
        statement_no=1,
    )

    db_session.add(lineage)
    db_session.flush()

    return (
        project,
        source_column,
        target_column,
    )


# ============================================================
# 2. 创建测试客户端
# ============================================================

def create_test_client(
    db_session,
) -> TestClient:
    def override_get_db():
        yield db_session

    app.dependency_overrides[
        get_db
    ] = override_get_db

    return TestClient(app)


# ============================================================
# 3. 查询项目全部字段
# ============================================================

def test_search_project_columns_api(
    db_session,
):
    (
        project,
        source_column,
        target_column,
    ) = create_column_search_api_data(
        db_session
    )

    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            (
                f"/api/projects/"
                f"{project.id}/columns"
            ),
            params={
                "page": 1,
                "page_size": 20,
            },
        )

        assert response.status_code == 200

        body = response.json()

        assert (
            body["project_id"]
            == project.id
        )

        assert (
            body["project_name"]
            == project.name
        )

        assert body["page"] == 1
        assert body["page_size"] == 20
        assert body["total"] == 2

        assert len(body["items"]) == 2

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 4. 按表名和字段名搜索
# ============================================================

def test_search_columns_with_filters_api(
    db_session,
):
    (
        project,
        source_column,
        target_column,
    ) = create_column_search_api_data(
        db_session
    )

    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            (
                f"/api/projects/"
                f"{project.id}/columns"
            ),
            params={
                "page": 1,
                "page_size": 20,
                "table_name": "dwd.orders",
                "column_name": "order_id",
            },
        )

        assert response.status_code == 200

        body = response.json()

        assert body["total"] == 1
        assert len(body["items"]) == 1

        item = body["items"][0]

        assert (
            item["column_id"]
            == target_column.id
        )

        assert (
            item["table_full_name"]
            == "dwd.orders"
        )

        assert (
            item["column_name"]
            == "order_id"
        )

        assert (
            item[
                "upstream_lineage_count"
            ]
            == 1
        )

        assert (
            item[
                "downstream_lineage_count"
            ]
            == 0
        )

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 5. 项目不存在返回404
# ============================================================

def test_column_search_api_returns_404(
    db_session,
):
    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            (
                "/api/projects/"
                "999999999/columns"
            )
        )

        assert response.status_code == 404

        assert (
            "血缘项目不存在"
            in response.json()["detail"]
        )

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 6. 非法分页参数返回422
# ============================================================

def test_column_search_api_rejects_pagination(
    db_session,
):
    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            "/api/projects/1/columns",
            params={
                "page": 0,
                "page_size": 20,
            },
        )

        assert response.status_code == 422

        response = client.get(
            "/api/projects/1/columns",
            params={
                "page": 1,
                "page_size": 101,
            },
        )

        assert response.status_code == 422

    finally:
        app.dependency_overrides.clear()
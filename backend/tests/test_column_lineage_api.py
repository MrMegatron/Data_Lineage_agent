from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
    SourceScript,
)


# ============================================================
# 1. 创建API测试数据
# ============================================================

def create_api_test_lineage(
    db_session,
):
    """
    创建：

        ods.orders.order_id
                ↓
        dwd.orders.order_id
                ↓
        ads.order_report.order_id
    """

    unique_suffix = uuid4().hex[:8]

    project = LineageProject(
        name=(
            "column_api_test_"
            f"{unique_suffix}"
        )
    )

    db_session.add(project)
    db_session.flush()

    ods_table = DataTable(
        project_id=project.id,
        schema_name="ods",
        table_name="orders",
        full_name="ods.orders",
        table_kind="physical",
    )

    dwd_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="orders",
        full_name="dwd.orders",
        table_kind="physical",
    )

    ads_table = DataTable(
        project_id=project.id,
        schema_name="ads",
        table_name="order_report",
        full_name="ads.order_report",
        table_kind="physical",
    )

    db_session.add_all(
        [
            ods_table,
            dwd_table,
            ads_table,
        ]
    )

    db_session.flush()

    ods_column = DataColumn(
        table_id=ods_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    dwd_column = DataColumn(
        table_id=dwd_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    ads_column = DataColumn(
        table_id=ads_table.id,
        column_name="order_id",
        ordinal_position=1,
        data_type="bigint",
    )

    db_session.add_all(
        [
            ods_column,
            dwd_column,
            ads_column,
        ]
    )

    db_session.flush()

    first_script = SourceScript(
        project_id=project.id,
        file_name="01_ods_to_dwd.sql",
        relative_path=(
            f"tests/{unique_suffix}/"
            "01_ods_to_dwd.sql"
        ),
        dialect="hive",
        file_hash="c" * 64,
        source_code=(
            "INSERT INTO dwd.orders "
            "(order_id) "
            "SELECT order_id "
            "FROM ods.orders"
        ),
        parse_status="success",
    )

    second_script = SourceScript(
        project_id=project.id,
        file_name="02_dwd_to_ads.sql",
        relative_path=(
            f"tests/{unique_suffix}/"
            "02_dwd_to_ads.sql"
        ),
        dialect="hive",
        file_hash="d" * 64,
        source_code=(
            "INSERT INTO ads.order_report "
            "(order_id) "
            "SELECT order_id "
            "FROM dwd.orders"
        ),
        parse_status="success",
    )

    db_session.add_all(
        [
            first_script,
            second_script,
        ]
    )

    db_session.flush()

    first_lineage = ColumnLineage(
        project_id=project.id,
        script_id=first_script.id,
        source_column_id=ods_column.id,
        target_column_id=dwd_column.id,
        relation_type="direct",
        resolution_status="confirmed",
        expression_text="order_id",
        statement_no=1,
    )

    second_lineage = ColumnLineage(
        project_id=project.id,
        script_id=second_script.id,
        source_column_id=dwd_column.id,
        target_column_id=ads_column.id,
        relation_type="direct",
        resolution_status="confirmed",
        expression_text="order_id",
        statement_no=1,
    )

    db_session.add_all(
        [
            first_lineage,
            second_lineage,
        ]
    )

    db_session.flush()

    first_evidence = LineageEvidence(
        column_lineage_id=first_lineage.id,
        script_id=first_script.id,
        statement_no=1,
        evidence_order=1,
        code_snippet="order_id",
        expression_text="order_id",
    )

    second_evidence = LineageEvidence(
        column_lineage_id=second_lineage.id,
        script_id=second_script.id,
        statement_no=1,
        evidence_order=1,
        code_snippet="order_id",
        expression_text="order_id",
    )

    db_session.add_all(
        [
            first_evidence,
            second_evidence,
        ]
    )

    db_session.flush()

    return (
        project,
        ods_column,
        dwd_column,
        ads_column,
    )


# ============================================================
# 2. 创建使用测试Session的客户端
# ============================================================

def create_test_client(
    db_session,
) -> TestClient:
    """
    覆盖 FastAPI 正式数据库依赖。

    这样接口会使用 pytest 提供的 db_session，
    测试结束后由 conftest.py rollback。
    """

    def override_get_db():
        yield db_session

    app.dependency_overrides[
        get_db
    ] = override_get_db

    return TestClient(app)


# ============================================================
# 3. 查询中间字段
# ============================================================

def test_get_column_lineage_api(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        ads_column,
    ) = create_api_test_lineage(
        db_session
    )

    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            (
                f"/api/columns/"
                f"{dwd_column.id}/lineage"
            )
        )

        assert response.status_code == 200

        body = response.json()

        assert (
            body["project_id"]
            == project.id
        )

        assert (
            body["column"]["column_id"]
            == dwd_column.id
        )

        assert (
            body["column"]["table_full_name"]
            == "dwd.orders"
        )

        assert (
            body["column"]["column_name"]
            == "order_id"
        )

        assert body["upstream_count"] == 1
        assert body["downstream_count"] == 1

        upstream_edge = (
            body["upstream_edges"][0]
        )

        assert (
            upstream_edge[
                "source_column"
            ][
                "table_full_name"
            ]
            == "ods.orders"
        )

        assert (
            upstream_edge[
                "target_column"
            ][
                "table_full_name"
            ]
            == "dwd.orders"
        )

        assert (
            upstream_edge[
                "script_file_name"
            ]
            == "01_ods_to_dwd.sql"
        )

        assert (
            upstream_edge[
                "evidences"
            ][0][
                "code_snippet"
            ]
            == "order_id"
        )

        downstream_edge = (
            body["downstream_edges"][0]
        )

        assert (
            downstream_edge[
                "source_column"
            ][
                "table_full_name"
            ]
            == "dwd.orders"
        )

        assert (
            downstream_edge[
                "target_column"
            ][
                "table_full_name"
            ]
            == "ads.order_report"
        )

        assert (
            downstream_edge[
                "script_file_name"
            ]
            == "02_dwd_to_ads.sql"
        )

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 4. 字段不存在返回404
# ============================================================

def test_column_lineage_api_returns_404(
    db_session,
):
    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            "/api/columns/999999999/lineage"
        )

        assert response.status_code == 404

        body = response.json()

        assert (
            "数据字段不存在"
            in body["detail"]
        )

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 5. 非法column_id返回422
# ============================================================

def test_column_lineage_api_rejects_invalid_id(
    db_session,
):
    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            "/api/columns/0/lineage"
        )

        assert response.status_code == 422

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 6. 查询双向多层字段血缘
# ============================================================

def test_get_column_lineage_graph_api(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        ads_column,
    ) = create_api_test_lineage(
        db_session
    )

    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            (
                f"/api/columns/"
                f"{dwd_column.id}/"
                "lineage/graph"
            ),
            params={
                "direction": "both",
                "max_depth": 10,
            },
        )

        assert response.status_code == 200

        body = response.json()

        assert (
            body["project_id"]
            == project.id
        )

        assert (
            body["root_column"][
                "column_id"
            ]
            == dwd_column.id
        )

        assert (
            body["root_column"][
                "table_full_name"
            ]
            == "dwd.orders"
        )

        assert (
            body["direction"]
            == "both"
        )

        assert (
            body["max_depth"]
            == 10
        )

        # ods、dwd、ads
        assert body["node_count"] == 3

        # ods -> dwd
        # dwd -> ads
        assert body["edge_count"] == 2

        node_names = {
            (
                f"{node['table_full_name']}."
                f"{node['column_name']}"
            )
            for node in body["nodes"]
        }

        assert node_names == {
            "ods.orders.order_id",
            "dwd.orders.order_id",
            "ads.order_report.order_id",
        }

        # dwd 是中间节点，
        # 两条边都距离根节点一层。
        assert all(
            edge["depth"] == 1
            for edge in body["edges"]
        )

        traversal_directions = {
            edge[
                "traversal_direction"
            ]
            for edge in body["edges"]
        }

        assert traversal_directions == {
            "upstream",
            "downstream",
        }

        # 每条血缘都应携带证据。
        assert all(
            len(
                edge["lineage"][
                    "evidences"
                ]
            )
            == 1
            for edge in body["edges"]
        )

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 7. 查询一层上游
# ============================================================

def test_get_upstream_graph_with_depth_one(
    db_session,
):
    (
        project,
        ods_column,
        dwd_column,
        ads_column,
    ) = create_api_test_lineage(
        db_session
    )

    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            (
                f"/api/columns/"
                f"{ads_column.id}/"
                "lineage/graph"
            ),
            params={
                "direction": "upstream",
                "max_depth": 1,
            },
        )

        assert response.status_code == 200

        body = response.json()

        assert (
            body["direction"]
            == "upstream"
        )

        assert body["max_depth"] == 1

        # 当前 ads + 上游 dwd
        assert body["node_count"] == 2
        assert body["edge_count"] == 1

        edge = body["edges"][0]

        assert edge["depth"] == 1

        assert (
            edge[
                "traversal_direction"
            ]
            == "upstream"
        )

        assert (
            edge["lineage"][
                "source_column"
            ][
                "table_full_name"
            ]
            == "dwd.orders"
        )

        assert (
            edge["lineage"][
                "target_column"
            ][
                "table_full_name"
            ]
            == "ads.order_report"
        )

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 8. 非法direction返回422
# ============================================================

def test_graph_api_rejects_invalid_direction(
    db_session,
):
    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            "/api/columns/1/lineage/graph",
            params={
                "direction": "left",
                "max_depth": 10,
            },
        )

        assert response.status_code == 422

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 9. 非法max_depth返回422
# ============================================================

def test_graph_api_rejects_invalid_depth(
    db_session,
):
    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            "/api/columns/1/lineage/graph",
            params={
                "direction": "both",
                "max_depth": 0,
            },
        )

        assert response.status_code == 422

        response = client.get(
            "/api/columns/1/lineage/graph",
            params={
                "direction": "both",
                "max_depth": 21,
            },
        )

        assert response.status_code == 422

    finally:
        app.dependency_overrides.clear()


# ============================================================
# 10. 不存在的字段返回404
# ============================================================

def test_graph_api_returns_404(
    db_session,
):
    client = create_test_client(
        db_session
    )

    try:
        response = client.get(
            (
                "/api/columns/"
                "999999999/"
                "lineage/graph"
            ),
            params={
                "direction": "both",
                "max_depth": 10,
            },
        )

        assert response.status_code == 404

        assert (
            "数据字段不存在"
            in response.json()["detail"]
        )

    finally:
        app.dependency_overrides.clear()
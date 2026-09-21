import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


# ============================================================
# 创建FastAPI测试客户端
# ============================================================

client = TestClient(app)


# ============================================================
# 1. 测试根接口
# ============================================================

def test_root_endpoint() -> None:
    response = client.get("/")

    assert response.status_code == 200

    response_data = response.json()

    assert response_data == {
        "system": settings.app_name,
        "version": "0.1.0",
        "status": "running",
    }


# ============================================================
# 2. 测试数据库健康检查
# ============================================================

@pytest.mark.integration
def test_health_endpoint() -> None:
    response = client.get(
        "/api/health"
    )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["status"] == "ok"

    assert (
        response_data["database"]
        == "connected"
    )

    assert (
        response_data["database_name"]
        == settings.db_name
    )

    assert (
        response_data["check_result"]
        == 1
    )


# ============================================================
# 3. 测试Vue开发服务器的CORS
# ============================================================

def test_vue_cors_preflight() -> None:
    response = client.options(
        "/api/health",
        headers={
            "Origin": (
                "http://localhost:5173"
            ),
            "Access-Control-Request-Method": (
                "GET"
            ),
        },
    )

    assert response.status_code == 200

    assert (
        response.headers[
            "access-control-allow-origin"
        ]
        == "http://localhost:5173"
    )
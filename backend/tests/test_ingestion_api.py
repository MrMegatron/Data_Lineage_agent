from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.main import app
from app.models import (
    LineageProject,
    ScriptTableAccess,
    SourceScript,
)


# ============================================================
# 1. 测试项目创建辅助函数
# ============================================================

def create_test_project(
    db_session,
    prefix: str,
) -> LineageProject:
    project = LineageProject(
        name=(
            f"{prefix}_{uuid4().hex[:8]}"
        ),
        description="ingestion API pytest",
    )

    db_session.add(project)
    db_session.flush()

    return project


# ============================================================
# 2. 创建测试客户端
# ============================================================

@pytest.fixture
def api_client(
    db_session,
    monkeypatch,
):
    """
    使用测试数据库 Session 替换 FastAPI 的 get_db。

    同时把 endpoint 中的 commit 替换为 flush，
    这样测试结束后 conftest.py 仍然可以 rollback。
    """

    def override_get_db():
        yield db_session

    app.dependency_overrides[
        get_db
    ] = override_get_db

    # 正式运行时 commit 会永久提交。
    #
    # 自动测试时将 commit 替换成 flush，
    # 避免测试数据永久留在数据库。
    monkeypatch.setattr(
        db_session,
        "commit",
        db_session.flush,
    )

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


# ============================================================
# 3. 测试成功导入 SQL 目录
# ============================================================

def test_ingestion_api_success(
    api_client,
    db_session,
    tmp_path,
    monkeypatch,
):
    project = create_test_project(
        db_session,
        "api_success_project",
    )

    sql_root = tmp_path / "sql_sources"
    project_directory = (
        sql_root
        / "hive_project"
        / "hive"
    )

    project_directory.mkdir(
        parents=True
    )

    sql_file = (
        project_directory
        / "orders.sql"
    )

    sql_file.write_text(
        (
            "INSERT INTO dwd.orders\n"
            "SELECT * FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        settings,
        "sql_source_root",
        sql_root,
    )

    response = api_client.post(
        (
            f"/api/projects/{project.id}"
            "/ingest-directory"
        ),
        json={
            "relative_directory":
                "hive_project",
        },
    )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["status"] == "success"
    assert response_data["total_files"] == 1
    assert response_data["success_files"] == 1
    assert response_data["failed_files"] == 0
    assert response_data["created_scripts"] == 1
    assert response_data["table_access_count"] == 2
    # 这个测试只有一个脚本：
    #
    # READ  ods.orders
    # WRITE dwd.orders
    #
    # 没有下游脚本读取 dwd.orders，
    # 所以依赖数量是 0。
    assert (
        response_data[
            "total_dependency_count"
        ]
        == 0
    )

    assert (
        response_data[
            "confirmed_dependency_count"
        ]
        == 0
    )

    assert (
        response_data[
            "ambiguous_dependency_count"
        ]
        == 0
    )

    assert (
        response_data[
            "dependency_via_table_count"
        ]
        == 0
    )
    assert len(
        response_data["files"]
    ) == 1

    assert (
        response_data["files"][0]
        ["relative_path"]
        == "hive/orders.sql"
    )

    scripts = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).all()

    assert len(scripts) == 1

    accesses = db_session.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id
            == scripts[0].id
        )
    ).all()

    assert len(accesses) == 2


# ============================================================
# 4. 测试阻止目录越界
# ============================================================

def test_ingestion_api_rejects_path_traversal(
    api_client,
    db_session,
    tmp_path,
    monkeypatch,
):
    project = create_test_project(
        db_session,
        "path_security_project",
    )

    sql_root = tmp_path / "sql_sources"
    sql_root.mkdir()

    monkeypatch.setattr(
        settings,
        "sql_source_root",
        sql_root,
    )

    response = api_client.post(
        (
            f"/api/projects/{project.id}"
            "/ingest-directory"
        ),
        json={
            "relative_directory": "../outside",
        },
    )

    assert response.status_code == 400

    assert (
        "超出了允许扫描"
        in response.json()["detail"]
    )


# ============================================================
# 5. 测试不存在的相对目录
# ============================================================

def test_ingestion_api_missing_directory(
    api_client,
    db_session,
    tmp_path,
    monkeypatch,
):
    project = create_test_project(
        db_session,
        "missing_directory_project",
    )

    sql_root = tmp_path / "sql_sources"
    sql_root.mkdir()

    monkeypatch.setattr(
        settings,
        "sql_source_root",
        sql_root,
    )

    response = api_client.post(
        (
            f"/api/projects/{project.id}"
            "/ingest-directory"
        ),
        json={
            "relative_directory":
                "not_exists",
        },
    )

    assert response.status_code == 400

    assert (
        "扫描目录不存在"
        in response.json()["detail"]
    )


# ============================================================
# 6. 测试项目不存在
# ============================================================

def test_ingestion_api_missing_project(
    api_client,
    tmp_path,
    monkeypatch,
):
    sql_root = tmp_path / "sql_sources"
    sql_root.mkdir()

    sql_file = sql_root / "orders.sql"

    sql_file.write_text(
        "SELECT * FROM ods.orders;",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        settings,
        "sql_source_root",
        sql_root,
    )

    response = api_client.post(
        "/api/projects/-1/ingest-directory",
        json={
            "relative_directory": ".",
        },
    )

    assert response.status_code == 404

    assert (
        "血缘项目不存在"
        in response.json()["detail"]
    )


# ============================================================
# 7. 测试空目录
# ============================================================

def test_ingestion_api_empty_directory(
    api_client,
    db_session,
    tmp_path,
    monkeypatch,
):
    project = create_test_project(
        db_session,
        "empty_api_project",
    )

    sql_root = tmp_path / "sql_sources"
    empty_directory = (
        sql_root
        / "empty_project"
    )

    empty_directory.mkdir(
        parents=True
    )

    monkeypatch.setattr(
        settings,
        "sql_source_root",
        sql_root,
    )

    response = api_client.post(
        (
            f"/api/projects/{project.id}"
            "/ingest-directory"
        ),
        json={
            "relative_directory":
                "empty_project",
        },
    )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["status"] == "empty"
    assert response_data["total_files"] == 0
    assert response_data["files"] == []

# ============================================================
# 8. 测试明确指定 Hive 方言
# ============================================================

def test_ingestion_api_with_hive_override(
    api_client,
    db_session,
    tmp_path,
    monkeypatch,
):
    project = create_test_project(
        db_session,
        "hive_override_project",
    )

    sql_root = tmp_path / "sql_sources"
    source_directory = (
        sql_root
        / "business_project"
    )

    source_directory.mkdir(
        parents=True
    )

    sql_file = (
        source_directory
        / "orders.sql"
    )

    # 这段 SQL 是通用语法。
    # 自动识别通常会得到 unknown。
    sql_file.write_text(
        (
            "INSERT INTO dwd.orders\n"
            "SELECT * FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        settings,
        "sql_source_root",
        sql_root,
    )

    response = api_client.post(
        (
            f"/api/projects/{project.id}"
            "/ingest-directory"
        ),
        json={
            "relative_directory":
                "business_project",

            # 明确指定 Hive。
            "dialect": "hive",
        },
    )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["status"] == "success"

    assert (
        response_data["files"][0]["dialect"]
        == "hive"
    )

    scripts = db_session.scalars(
        select(SourceScript).where(
            SourceScript.project_id
            == project.id
        )
    ).all()

    assert len(scripts) == 1
    assert scripts[0].dialect == "hive"
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import engine
from app.routers.ingestion import (
    router as ingestion_router,
)
from app.routers.dag import (
    router as dag_router,
)
from app.routers.script_detail import (
    router as script_detail_router,
)

from app.routers.table_detail import (
    router as table_detail_router,
)

from app.routers.catalog import (
    router as catalog_router,
)

# ============================================================
# 1. 创建日志记录器
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# 2. 创建FastAPI应用
# ============================================================

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Data Lineage Agent Backend API"
    ),
)


# ============================================================
# 3. 配置CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,

    # Vue本地开发服务器允许访问后端。
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],

    # 当前版本没有Cookie登录。
    allow_credentials=False,

    # 允许常用HTTP方法。
    allow_methods=["*"],

    # 允许前端发送常用请求头。
    allow_headers=["*"],
)

# ============================================================
# 注册业务路由
# ============================================================

app.include_router(
    ingestion_router
)
app.include_router(
    dag_router
)
app.include_router(
    script_detail_router
)
app.include_router(
    table_detail_router
)
app.include_router(
    catalog_router
)
# ============================================================
# 4. 根接口
# ============================================================

@app.get("/")
def root() -> dict[str, str]:
    """
    返回后端基础状态。

    该接口不连接数据库，
    只证明FastAPI服务已经启动。
    """

    return {
        "system": settings.app_name,
        "version": "0.1.0",
        "status": "running",
    }


# ============================================================
# 5. 数据库健康检查接口
# ============================================================

@app.get("/api/health")
def health_check() -> dict[str, Any]:
    """
    检查后端和MySQL之间的连接。

    成功：
        HTTP 200

    数据库连接失败：
        HTTP 503
    """

    try:
        with engine.connect() as connection:
            select_result = connection.execute(
                text("SELECT 1")
            ).scalar_one()

            current_database = (
                connection.execute(
                    text("SELECT DATABASE()")
                ).scalar_one()
            )

        return {
            "status": "ok",
            "database": "connected",
            "database_name": current_database,
            "check_result": select_result,
        }

    except SQLAlchemyError as exc:
        # 详细异常只写入后端日志，
        # 不把数据库内部错误直接返回给前端。
        logger.exception(
            "Database health check failed"
        )

        raise HTTPException(
            status_code=(
                status
                .HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail={
                "status": "error",
                "database": "disconnected",
                "message": (
                    "Database health check failed"
                ),
            },
        ) from exc
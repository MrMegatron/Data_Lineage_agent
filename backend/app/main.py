from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import engine


# ============================================================
# 1. 创建FastAPI应用
# ============================================================

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Data Lineage Agent API",
)


# ============================================================
# 2. CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,

    # Vue开发服务器
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],

    # 第一版本暂时没有Cookie登录
    allow_credentials=False,

    allow_methods=["*"],

    allow_headers=["*"],
)


# ============================================================
# 3. 根接口
# ============================================================

@app.get("/")
def root():

    return {
        "system": settings.app_name,
        "version": "0.1.0",
        "status": "running",
    }


# ============================================================
# 4. 健康检查
# ============================================================

@app.get("/api/health")
def health_check():

    try:

        with engine.connect() as connection:

            connection.execute(
                text("SELECT 1")
            )

        return {
            "status": "ok",
            "database": "connected",
        }

    except Exception as exc:

        return {
            "status": "error",
            "database": "disconnected",
            "message": str(exc),
        }

@app.get("/api/haha")
def haha():
    return "哈哈哈哈哈哈哈"
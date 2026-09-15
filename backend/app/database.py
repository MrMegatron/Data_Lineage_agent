from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


# ============================================================
# 1. 创建数据库引擎
# ============================================================

engine = create_engine(
    settings.database_url,

    # 每次从连接池拿连接之前，
    # 先检查这个MySQL连接是否仍然有效。
    pool_pre_ping=True,

    # SQL连接池大小
    pool_size=10,

    # 当10个连接都被占用后，
    # 最多额外允许20个临时连接。
    max_overflow=20,

    # 开发阶段暂时关闭SQL日志。
    echo=False,
)

# ---------------------------------------------------------
# 数据库约束命名规范
#
# 作用：
# Alembic 自动生成 migration 时，
# PRIMARY KEY / FOREIGN KEY / INDEX 等名称保持稳定。
# ---------------------------------------------------------
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
# ============================================================
# 2. 创建数据库Session工厂
# ============================================================

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


# ============================================================
# 3. 所有ORM Model的基类
# ============================================================

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ============================================================
# 4. FastAPI数据库依赖
# ============================================================

def get_db():
    """
    为每一个HTTP请求创建独立数据库Session。

    请求结束以后自动关闭连接。
    """

    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()
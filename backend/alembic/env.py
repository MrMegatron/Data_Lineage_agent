from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from app import models  # noqa: F401
from app.config import settings
from app.database import Base


# ============================================================
# 1. 取得 Alembic Config 对象
# ============================================================

config = context.config


# ============================================================
# 2. 把应用数据库 URL 写入 Alembic 运行时配置
# ============================================================

database_url = settings.database_url.render_as_string(
    hide_password=False,
)

# ConfigParser 会把 % 当作配置插值符号。
# 如果数据库密码中包含 %，必须转换成 %%。
config.set_main_option(
    "sqlalchemy.url",
    database_url.replace("%", "%%"),
)


# ============================================================
# 3. 初始化 Alembic 日志
# ============================================================

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ============================================================
# 4. 指定 Alembic 自动比较的 ORM Metadata
# ============================================================

target_metadata = Base.metadata


# ============================================================
# 5. 离线 migration
# ============================================================

def run_migrations_offline() -> None:
    """
    离线模式不直接建立数据库连接。

    常用于生成 SQL 脚本，例如：
        alembic upgrade head --sql
    """

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ============================================================
# 6. 使用已建立的数据库连接执行 migration
# ============================================================

def do_run_migrations(
    connection: Connection,
) -> None:
    """
    将数据库连接交给 Alembic，
    再运行当前需要执行的 migration。
    """

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ============================================================
# 7. 在线 migration
# ============================================================

def run_migrations_online() -> None:
    """
    在线模式会连接 .env 指定的 MySQL。

    Alembic revision --autogenerate、
    Alembic upgrade head 等命令会使用该模式。
    """

    connectable = engine_from_config(
        config.get_section(
            config.config_ini_section,
            {},
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        with connectable.connect() as connection:
            do_run_migrations(connection)
    finally:
        connectable.dispose()


# ============================================================
# 8. 根据 Alembic 模式选择执行函数
# ============================================================

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
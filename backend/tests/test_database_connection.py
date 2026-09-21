import pytest
from sqlalchemy import text

from app.config import settings
from app.database import engine


# ============================================================
# 1. 测试最基础的数据库连接
# ============================================================

@pytest.mark.integration
def test_mysql_connection() -> None:
    """
    执行 SELECT 1，验证：
    - MySQL服务可以访问；
    - 用户名和密码正确；
    - SQLAlchemy Engine工作正常。
    """

    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT 1")
        )

        value = result.scalar_one()

    assert value == 1


# ============================================================
# 2. 测试当前连接的数据库
# ============================================================

@pytest.mark.integration
def test_current_database() -> None:
    """
    确认程序实际连接的数据库，
    与 .env 中的 DB_NAME 完全一致。
    """

    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT DATABASE()")
        )

        current_database = result.scalar_one()

    assert current_database == settings.db_name, (
        "当前连接的数据库与 .env 不一致："
        f"预期={settings.db_name}，"
        f"实际={current_database}"
    )


# ============================================================
# 3. 测试MySQL连接字符集
# ============================================================

@pytest.mark.integration
def test_mysql_connection_charset() -> None:
    """
    确认数据库连接使用 utf8mb4，
    避免后续中文SQL源码或错误信息乱码。
    """

    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT @@character_set_connection")
        )

        connection_charset = result.scalar_one()

    assert connection_charset.lower() == "utf8mb4", (
        "MySQL连接字符集不是utf8mb4："
        f"实际={connection_charset}"
    )


# ============================================================
# 4. 测试MySQL版本能够正常读取
# ============================================================

@pytest.mark.integration
def test_mysql_version() -> None:
    """
    确认当前连接确实是MySQL兼容数据库，
    并且能够读取数据库版本。
    """

    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT VERSION()")
        )

        mysql_version = result.scalar_one()

    assert mysql_version
    assert isinstance(mysql_version, str)
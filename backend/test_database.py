from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import engine


def main() -> None:
    """
    手工测试 SQLAlchemy Engine 是否能够连接 MySQL。

    只执行 SELECT，不创建表，也不写入数据。
    """

    print("========== 数据库连接测试 ==========")
    print(f"数据库地址：{settings.db_host}")
    print(f"数据库端口：{settings.db_port}")
    print(f"数据库名称：{settings.db_name}")
    print(f"数据库用户：{settings.db_user}")
    print("数据库密码：**********")

    try:
        with engine.connect() as connection:
            select_one = connection.execute(
                text("SELECT 1")
            ).scalar_one()

            mysql_version = connection.execute(
                text("SELECT VERSION()")
            ).scalar_one()

            current_database = connection.execute(
                text("SELECT DATABASE()")
            ).scalar_one()

            connection_charset = connection.execute(
                text("SELECT @@character_set_connection")
            ).scalar_one()

        print("------------------------------------")
        print("连接状态：成功")
        print(f"SELECT 1：{select_one}")
        print(f"MySQL版本：{mysql_version}")
        print(f"当前数据库：{current_database}")
        print(f"连接字符集：{connection_charset}")
        print("========== 测试通过 ==========")

    except SQLAlchemyError as exc:
        print("------------------------------------")
        print("连接状态：失败")
        print(f"错误类型：{type(exc).__name__}")
        print(f"错误信息：{exc}")
        print("========== 测试失败 ==========")

        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
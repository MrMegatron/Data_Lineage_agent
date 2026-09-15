from sqlalchemy import text

from app.database import engine


def main():

    try:

        with engine.connect() as connection:

            # 测试最基本数据库连接
            result = connection.execute(
                text("SELECT 1")
            )

            value = result.scalar()

            # 查询MySQL版本
            version_result = connection.execute(
                text("SELECT VERSION()")
            )

            version = version_result.scalar()

            # 查询当前数据库
            database_result = connection.execute(
                text("SELECT DATABASE()")
            )

            database_name = database_result.scalar()

            print("========== MySQL连接测试 ==========")

            print("连接状态：成功")

            print("SELECT 1结果：", value)

            print("MySQL版本：", version)

            print("当前数据库：", database_name)

    except Exception as exc:

        print("========== MySQL连接测试 ==========")

        print("连接状态：失败")

        print("错误信息：")
        print(exc)


if __name__ == "__main__":
    main()
import sqlglot
from sqlglot import exp


def main():

    sql = """
    INSERT OVERWRITE TABLE dwd_order
    SELECT
        order_id,
        user_id,
        amount,
        CASE
            WHEN amount >= 1000 THEN 'HIGH'
            ELSE 'NORMAL'
        END AS order_level
    FROM ods_order
    """

    # ========================================================
    # 1. 解析Hive SQL
    # ========================================================

    expression = sqlglot.parse_one(
        sql,
        read="hive",
    )

    print("========== 原始AST ==========")

    print(expression)

    print()

    # ========================================================
    # 2. 查找所有表
    # ========================================================

    print("========== TABLE ==========")

    for table in expression.find_all(exp.Table):

        print(
            "表名:",
            table.name
        )

    print()

    # ========================================================
    # 3. 查找所有字段
    # ========================================================

    print("========== COLUMN ==========")

    for column in expression.find_all(exp.Column):

        print(
            "字段:",
            column.sql()
        )

    print()

    # ========================================================
    # 4. 查找SELECT表达式
    # ========================================================

    print("========== SELECT EXPRESSIONS ==========")

    select = expression.find(exp.Select)

    if select:

        for item in select.expressions:

            print(
                "表达式:",
                item.sql()
            )

            print(
                "输出字段:",
                item.alias_or_name
            )

            print("---")


if __name__ == "__main__":
    main()
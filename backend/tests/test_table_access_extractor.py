from app.services.sql_parser import (
    parse_sql_script,
)
from app.services.table_access_extractor import (
    extract_script_table_accesses,
)


def access_pairs(accesses):
    """
    将访问结果转换成方便断言的集合。

    示例：

        {
            ("read", "ods.orders"),
            ("write", "dwd.orders"),
        }
    """

    return {
        (
            access.access_type,
            access.full_name,
        )
        for access in accesses
    }


# ============================================================
# 1. 测试普通 SELECT
# ============================================================

def test_extract_select_read_tables():
    sql = """
    SELECT
        o.order_id,
        c.customer_name
    FROM ods.orders o
    JOIN dim.customers c
      ON o.customer_id = c.customer_id
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert access_pairs(accesses) == {
        ("read", "ods.orders"),
        ("read", "dim.customers"),
    }


# ============================================================
# 2. 测试 INSERT 的 READ 和 WRITE
# ============================================================

def test_extract_insert_read_and_write():
    sql = """
    INSERT INTO dwd.orders
    SELECT *
    FROM ods.orders
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert access_pairs(accesses) == {
        ("read", "ods.orders"),
        ("write", "dwd.orders"),
    }


# ============================================================
# 3. 测试排除 CTE
# ============================================================

def test_cte_is_not_physical_table():
    sql = """
    WITH recent_orders AS (
        SELECT *
        FROM ods.orders
    ),
    valid_orders AS (
        SELECT *
        FROM recent_orders
        WHERE order_id IS NOT NULL
    )
    SELECT
        v.order_id,
        c.customer_name
    FROM valid_orders v
    JOIN dim.customers c
      ON v.customer_id = c.customer_id
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert access_pairs(accesses) == {
        ("read", "ods.orders"),
        ("read", "dim.customers"),
    }

    table_names = {
        access.full_name
        for access in accesses
    }

    assert "recent_orders" not in table_names
    assert "valid_orders" not in table_names


# ============================================================
# 4. 测试 CREATE TABLE AS SELECT
# ============================================================

def test_create_table_as_select():
    sql = """
    CREATE TABLE dwd.order_summary AS
    SELECT
        customer_id,
        COUNT(*) AS order_count
    FROM ods.orders
    GROUP BY customer_id
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert access_pairs(accesses) == {
        ("read", "ods.orders"),
        ("write", "dwd.order_summary"),
    }


# ============================================================
# 5. 测试 PostgreSQL UPDATE FROM
# ============================================================

def test_update_from_read_and_write():
    sql = """
    UPDATE dwd.orders AS target
    SET amount = source.amount
    FROM staging.order_updates AS source
    WHERE target.order_id = source.order_id
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="postgresql",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert access_pairs(accesses) == {
        ("read", "staging.order_updates"),
        ("write", "dwd.orders"),
    }


# ============================================================
# 6. 测试同一张表既读又写
# ============================================================

def test_same_table_can_be_read_and_written():
    sql = """
    INSERT INTO dwd.orders
    SELECT *
    FROM dwd.orders
    WHERE order_id IS NOT NULL
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert access_pairs(accesses) == {
        ("read", "dwd.orders"),
        ("write", "dwd.orders"),
    }


# ============================================================
# 7. 测试同一张表重复读取时去重
# ============================================================

def test_duplicate_reads_are_deduplicated():
    sql = """
    SELECT
        child.order_id,
        parent.order_id AS parent_order_id
    FROM ods.orders child
    JOIN ods.orders parent
      ON child.parent_id = parent.order_id
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert access_pairs(accesses) == {
        ("read", "ods.orders"),
    }

    assert len(accesses) == 1


# ============================================================
# 8. 测试三段式表名
# ============================================================

def test_three_part_table_name():
    sql = """
    SELECT *
    FROM hive_catalog.ods.orders
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="spark",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert len(accesses) == 1

    access = accesses[0]

    assert access.access_type == "read"
    assert access.catalog_name == "hive_catalog"
    assert access.schema_name == "ods"
    assert access.table_name == "orders"

    assert access.full_name == (
        "hive_catalog.ods.orders"
    )


# ============================================================
# 9. 测试带引号的 PostgreSQL 表名
# ============================================================

def test_quoted_postgresql_table_name():
    sql = """
    SELECT *
    FROM "Sales"."OrderDetail"
    """

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="postgresql",
    )

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert len(accesses) == 1

    access = accesses[0]

    assert access.schema_name == "Sales"
    assert access.table_name == "OrderDetail"
    assert access.full_name == (
        "Sales.OrderDetail"
    )


# ============================================================
# 10. 测试失败语句被跳过
# ============================================================

def test_failed_statement_is_skipped():
    sql = (
        "SELECT (1 +;\n"
        "SELECT * FROM ods.valid_orders;\n"
    )

    parse_result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    assert parse_result.parse_status == "failed"
    assert parse_result.failed_count == 1
    assert parse_result.success_count == 1

    accesses = extract_script_table_accesses(
        parse_result
    )

    assert access_pairs(accesses) == {
        ("read", "ods.valid_orders"),
    }
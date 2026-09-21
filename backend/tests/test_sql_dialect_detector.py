import pytest

from app.services.sql_dialect_detector import (
    detect_sql_dialect,
    get_sqlglot_dialect,
)


# ============================================================
# 1. 测试 Spark SQL
# ============================================================

def test_detect_spark_sql():
    sql = """
    CREATE OR REPLACE TEMP VIEW order_view
    USING DELTA
    AS
    SELECT *
    FROM bronze.orders
    """

    result = detect_sql_dialect(
        source_code=sql,
        relative_path="models/orders.sql",
    )

    assert result.dialect == "spark"
    assert result.confidence == "high"
    assert len(result.reasons) >= 1


# ============================================================
# 2. 测试 Hive SQL
# ============================================================

def test_detect_hive_sql():
    sql = """
    CREATE TABLE ods.orders (
        order_id STRING,
        amount DECIMAL(18, 2)
    )
    ROW FORMAT DELIMITED
    FIELDS TERMINATED BY ','
    STORED AS TEXTFILE
    """

    result = detect_sql_dialect(
        source_code=sql,
        relative_path="models/orders.sql",
    )

    assert result.dialect == "hive"
    assert result.confidence == "high"

    assert any(
        "ROW FORMAT" in reason
        for reason in result.reasons
    )


# ============================================================
# 3. 测试 PostgreSQL
# ============================================================

def test_detect_postgresql_sql():
    sql = """
    SELECT DISTINCT ON (customer_id)
        customer_id,
        amount::numeric
    FROM sales.orders
    WHERE customer_name ILIKE '%test%'
    """

    result = detect_sql_dialect(
        source_code=sql,
        relative_path="reports/orders.sql",
    )

    assert result.dialect == "postgresql"
    assert result.confidence == "high"
    assert len(result.reasons) >= 2


# ============================================================
# 4. 测试根据路径识别方言
# ============================================================

def test_detect_dialect_from_path():
    sql = """
    SELECT order_id
    FROM orders
    """

    result = detect_sql_dialect(
        source_code=sql,
        relative_path=(
            "projects/postgresql/"
            "order_report.sql"
        ),
    )

    assert result.dialect == "postgresql"
    assert result.confidence == "high"

    assert any(
        "文件路径" in reason
        for reason in result.reasons
    )


# ============================================================
# 5. 测试普通 SQL 返回 unknown
# ============================================================

def test_generic_sql_returns_unknown():
    sql = """
    SELECT
        order_id,
        amount
    FROM orders
    WHERE amount > 100
    """

    result = detect_sql_dialect(
        source_code=sql,
        relative_path="models/orders.sql",
    )

    assert result.dialect == "unknown"
    assert result.confidence == "unknown"


# ============================================================
# 6. 测试证据冲突时返回 unknown
# ============================================================

def test_tied_dialect_scores_return_unknown():
    sql = """
    SELECT *
    FROM orders
    """

    result = detect_sql_dialect(
        source_code=sql,
        relative_path=(
            "spark/hive/orders.sql"
        ),
    )

    assert result.dialect == "unknown"
    assert result.confidence == "unknown"

    assert any(
        "得分并列" in reason
        for reason in result.reasons
    )


# ============================================================
# 7. 测试 SQLGlot 方言名称映射
# ============================================================

@pytest.mark.parametrize(
    (
        "business_dialect",
        "expected_sqlglot_dialect",
    ),
    [
        ("hive", "hive"),
        ("spark", "spark"),
        ("postgresql", "postgres"),
        ("unknown", None),
    ],
)
def test_get_sqlglot_dialect(
    business_dialect,
    expected_sqlglot_dialect,
):
    result = get_sqlglot_dialect(
        business_dialect
    )

    assert result == expected_sqlglot_dialect
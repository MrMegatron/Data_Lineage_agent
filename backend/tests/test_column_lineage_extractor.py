import pytest

from app.services.column_lineage_extractor import (
    UnsupportedColumnLineageError,
    extract_direct_column_lineage,
)


# ============================================================
# 1. 测试三个直接字段映射
# ============================================================

def test_extract_three_direct_columns():
    sql_text = """
    INSERT INTO dwd.orders (
        order_id,
        customer_id,
        amount
    )
    SELECT
        order_id,
        customer_id,
        amount
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert (
        result.source_table_full_name
        == "ods.orders"
    )

    assert (
        result.target_table_full_name
        == "dwd.orders"
    )

    assert len(result.mappings) == 3

    first_mapping = result.mappings[0]

    assert (
        first_mapping.source_column_name
        == "order_id"
    )

    assert (
        first_mapping.target_column_name
        == "order_id"
    )

    assert (
        first_mapping.relation_type
        == "direct"
    )

    second_mapping = result.mappings[1]

    assert (
        second_mapping.source_column_name
        == "customer_id"
    )

    assert (
        second_mapping.target_column_name
        == "customer_id"
    )

    third_mapping = result.mappings[2]

    assert (
        third_mapping.source_column_name
        == "amount"
    )

    assert (
        third_mapping.target_column_name
        == "amount"
    )


# ============================================================
# 2. 测试来源字段名和目标字段名不同
# ============================================================

def test_map_different_source_and_target_names():
    sql_text = """
    INSERT INTO dwd.orders (
        order_id
    )
    SELECT
        source_order_id
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    mapping = result.mappings[0]

    assert (
        mapping.source_column_name
        == "source_order_id"
    )

    assert (
        mapping.target_column_name
        == "order_id"
    )


# ============================================================
# 3. 测试简单 AS 别名
# ============================================================

def test_extract_direct_column_with_alias():
    sql_text = """
    INSERT INTO dwd.orders (
        order_id
    )
    SELECT
        source_order_id AS order_id
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    mapping = result.mappings[0]

    assert (
        mapping.source_column_name
        == "source_order_id"
    )

    assert (
        mapping.target_column_name
        == "order_id"
    )

    assert (
        mapping.expression_text
        == "source_order_id AS order_id"
    )


# ============================================================
# 4. 没有目标字段时必须拒绝
# ============================================================

def test_reject_insert_without_target_columns():
    sql_text = """
    INSERT INTO dwd.orders
    SELECT
        order_id,
        amount
    FROM ods.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="显式声明目标字段",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


# ============================================================
# 5. 目标字段数量不一致时必须拒绝
# ============================================================

def test_reject_column_count_mismatch():
    sql_text = """
    INSERT INTO dwd.orders (
        order_id,
        amount
    )
    SELECT
        order_id
    FROM ods.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="字段数量不一致",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


# ============================================================
# 6. 暂时拒绝 SELECT *
# ============================================================

def test_reject_select_star():
    sql_text = """
    INSERT INTO dwd.orders (
        order_id,
        amount
    )
    SELECT *
    FROM ods.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match=r"SELECT \*",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


# ============================================================
# 7. 暂时拒绝计算表达式
# ============================================================

def test_reject_transform_expression():
    sql_text = """
    INSERT INTO dwd.orders (
        amount
    )
    SELECT
        price * quantity
    FROM ods.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="只支持直接字段映射",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


# ============================================================
# 8. 暂时拒绝 JOIN
# ============================================================

def test_reject_join():
    sql_text = """
    INSERT INTO dwd.order_detail (
        order_id
    )
    SELECT
        o.order_id
    FROM ods.orders AS o
    JOIN ods.customer AS c
        ON o.customer_id = c.customer_id;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="暂不支持 JOIN",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


# ============================================================
# 9. 非 INSERT SQL 必须拒绝
# ============================================================

def test_reject_plain_select():
    sql_text = """
    SELECT
        order_id
    FROM ods.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="只支持 INSERT INTO",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


# ============================================================
# 10. 空 SQL 必须拒绝
# ============================================================

def test_reject_empty_sql():
    with pytest.raises(
        ValueError,
        match="sql_text 不能为空",
    ):
        extract_direct_column_lineage(
            sql_text="   ",
            dialect="hive",
        )
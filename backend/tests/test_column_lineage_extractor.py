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

# ============================================================
# 7. 支持两个来源字段的转换表达式
# ============================================================

def test_extract_transform_expression():
    sql_text = """
    INSERT INTO dwd.orders (
        amount
    )
    SELECT
        price * quantity AS amount
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

    # 一个投影包含两个来源字段，
    # 因此生成两条Mapping。
    assert len(result.mappings) == 2

    source_column_names = {
        mapping.source_column_name
        for mapping in result.mappings
    }

    assert source_column_names == {
        "price",
        "quantity",
    }

    assert all(
        mapping.target_column_name
        == "amount"
        for mapping in result.mappings
    )

    assert all(
        mapping.relation_type
        == "transform"
        for mapping in result.mappings
    )

    assert all(
        mapping.expression_text
        == "price * quantity AS amount"
        for mapping in result.mappings
    )

    assert all(
        mapping.ordinal_position == 1
        for mapping in result.mappings
    )
# ============================================================
# 8. 支持JOIN和表别名
# ============================================================

def test_extract_join_with_aliases():
    sql_text = """
    INSERT INTO dwd.order_detail (
        order_id,
        customer_name
    )
    SELECT
        o.order_id,
        c.customer_name
    FROM ods.orders AS o
    JOIN ods.customer AS c
        ON o.customer_id = c.customer_id;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert (
        result.source_table_full_names
        == (
            "ods.orders",
            "ods.customer",
        )
    )

    assert (
        result.target_table_full_name
        == "dwd.order_detail"
    )

    assert len(result.mappings) == 2

    mappings_by_target = {
        mapping.target_column_name:
            mapping
        for mapping in result.mappings
    }

    order_mapping = (
        mappings_by_target["order_id"]
    )

    assert (
        order_mapping
        .source_table_full_name
        == "ods.orders"
    )

    assert (
        order_mapping.source_column_name
        == "order_id"
    )

    assert (
        order_mapping.relation_type
        == "direct"
    )

    customer_mapping = (
        mappings_by_target[
            "customer_name"
        ]
    )

    assert (
        customer_mapping
        .source_table_full_name
        == "ods.customer"
    )

    assert (
        customer_mapping
        .source_column_name
        == "customer_name"
    )

    assert (
        customer_mapping.relation_type
        == "direct"
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

# ============================================================
# 11. 支持聚合字段
# ============================================================

def test_extract_aggregate_expressions():
    sql_text = """
    INSERT INTO ads.order_summary (
        total_amount,
        order_count
    )
    SELECT
        SUM(amount) AS total_amount,
        COUNT(order_id) AS order_count
    FROM dwd.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert (
        result.source_table_full_name
        == "dwd.orders"
    )

    assert (
        result.target_table_full_name
        == "ads.order_summary"
    )

    assert len(result.mappings) == 2

    mappings_by_target = {
        mapping.target_column_name:
            mapping
        for mapping in result.mappings
    }

    total_amount_mapping = (
        mappings_by_target[
            "total_amount"
        ]
    )

    assert (
        total_amount_mapping
        .source_column_name
        == "amount"
    )

    assert (
        total_amount_mapping
        .relation_type
        == "aggregate"
    )

    assert (
        total_amount_mapping
        .expression_text
        == "SUM(amount) AS total_amount"
    )

    assert (
        total_amount_mapping
        .ordinal_position
        == 1
    )

    order_count_mapping = (
        mappings_by_target[
            "order_count"
        ]
    )

    assert (
        order_count_mapping
        .source_column_name
        == "order_id"
    )

    assert (
        order_count_mapping
        .relation_type
        == "aggregate"
    )

    assert (
        order_count_mapping
        .expression_text
        == "COUNT(order_id) AS order_count"
    )

    assert (
        order_count_mapping
        .ordinal_position
        == 2
    )


# ============================================================
# 12. COUNT(*)暂时不支持
# ============================================================

def test_reject_count_star():
    sql_text = """
    INSERT INTO ads.order_summary (
        row_count
    )
    SELECT
        COUNT(*) AS row_count
    FROM dwd.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match=r"\*",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


# ============================================================
# 13. 支持CASE WHEN转换字段
# ============================================================

def test_extract_case_when_expression():
    sql_text = """
    INSERT INTO dwd.orders (
        status_name,
        risk_level
    )
    SELECT
        CASE
            WHEN status = 1 THEN 'paid'
            ELSE 'unpaid'
        END AS status_name,
        CASE
            WHEN amount >= 1000
                 AND customer_level = 'vip'
            THEN 'high'
            ELSE 'normal'
        END AS risk_level
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

    # status -> status_name
    #
    # amount         \
    #                 -> risk_level
    # customer_level /
    assert len(result.mappings) == 3

    status_mappings = [
        mapping
        for mapping in result.mappings
        if (
            mapping.target_column_name
            == "status_name"
        )
    ]

    assert len(status_mappings) == 1

    assert (
        status_mappings[0]
        .source_column_name
        == "status"
    )

    assert (
        status_mappings[0]
        .relation_type
        == "transform"
    )

    assert (
        "CASE"
        in status_mappings[0]
        .expression_text
    )

    assert (
        "status"
        in status_mappings[0]
        .expression_text
    )

    risk_mappings = [
        mapping
        for mapping in result.mappings
        if (
            mapping.target_column_name
            == "risk_level"
        )
    ]

    assert len(risk_mappings) == 2

    risk_source_names = {
        mapping.source_column_name
        for mapping in risk_mappings
    }

    assert risk_source_names == {
        "amount",
        "customer_level",
    }

    assert all(
        mapping.relation_type
        == "transform"
        for mapping in risk_mappings
    )

    assert all(
        "CASE" in mapping.expression_text
        for mapping in risk_mappings
    )

    assert all(
        mapping.ordinal_position == 2
        for mapping in risk_mappings
    )

# ============================================================
# 14. 支持普通函数转换
# ============================================================

def test_extract_function_expressions():
    sql_text = """
    INSERT INTO dwd.customer (
        full_name,
        phone_clean,
        created_date
    )
    SELECT
        CONCAT(
            first_name,
            ' ',
            last_name
        ) AS full_name,
        COALESCE(
            phone,
            'unknown'
        ) AS phone_clean,
        DATE_FORMAT(
            created_at,
            'yyyy-MM-dd'
        ) AS created_date
    FROM ods.customer;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert (
        result.source_table_full_name
        == "ods.customer"
    )

    assert (
        result.target_table_full_name
        == "dwd.customer"
    )

    # first_name -> full_name
    # last_name  -> full_name
    # phone      -> phone_clean
    # created_at -> created_date
    assert len(result.mappings) == 4

    full_name_mappings = [
        mapping
        for mapping in result.mappings
        if (
            mapping.target_column_name
            == "full_name"
        )
    ]

    assert len(full_name_mappings) == 2

    assert {
        mapping.source_column_name
        for mapping in full_name_mappings
    } == {
        "first_name",
        "last_name",
    }

    assert all(
        mapping.relation_type
        == "transform"
        for mapping in full_name_mappings
    )

    assert all(
        "CONCAT" in mapping.expression_text
        for mapping in full_name_mappings
    )

    phone_mappings = [
        mapping
        for mapping in result.mappings
        if (
            mapping.target_column_name
            == "phone_clean"
        )
    ]

    assert len(phone_mappings) == 1

    assert (
        phone_mappings[0]
        .source_column_name
        == "phone"
    )

    assert (
        phone_mappings[0]
        .relation_type
        == "transform"
    )

    assert (
        "COALESCE"
        in phone_mappings[0]
        .expression_text
    )

    date_mappings = [
        mapping
        for mapping in result.mappings
        if (
            mapping.target_column_name
            == "created_date"
        )
    ]

    assert len(date_mappings) == 1

    assert (
        date_mappings[0]
        .source_column_name
        == "created_at"
    )

    assert (
        date_mappings[0]
        .relation_type
        == "transform"
    )

    assert (
        "DATE_FORMAT"
        in date_mappings[0]
        .expression_text
    )

# ============================================================
# 15. 支持跨表字段转换
# ============================================================

def test_extract_join_transform_expression():
    sql_text = """
    INSERT INTO dwd.order_detail (
        final_amount
    )
    SELECT
        o.amount * c.discount_rate
            AS final_amount
    FROM ods.orders AS o
    JOIN ods.customer AS c
        ON o.customer_id = c.customer_id;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert len(result.mappings) == 2

    actual_sources = {
        (
            mapping
            .source_table_full_name,
            mapping
            .source_column_name,
        )
        for mapping in result.mappings
    }

    assert actual_sources == {
        (
            "ods.orders",
            "amount",
        ),
        (
            "ods.customer",
            "discount_rate",
        ),
    }

    assert all(
        mapping.target_column_name
        == "final_amount"
        for mapping in result.mappings
    )

    assert all(
        mapping.relation_type
        == "transform"
        for mapping in result.mappings
    )


# ============================================================
# 16. JOIN中的未限定字段必须拒绝
# ============================================================

def test_reject_unqualified_column_in_join():
    sql_text = """
    INSERT INTO dwd.order_detail (
        order_id
    )
    SELECT
        order_id
    FROM ods.orders AS o
    JOIN ods.customer AS c
        ON o.customer_id = c.customer_id;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="必须使用表名或别名限定",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


# ============================================================
# 17. 支持单层单个CTE
# ============================================================

def test_extract_single_cte_lineage():
    sql_text = """
    WITH order_base AS (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.orders
    )
    INSERT INTO dwd.order_summary (
        order_id,
        amount
    )
    SELECT
        order_id,
        amount
    FROM order_base;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    # CTE不是物理表。
    assert (
        result.source_table_full_names
        == (
            "ods.orders",
        )
    )

    assert (
        result.target_table_full_name
        == "dwd.order_summary"
    )

    # order_id -> order_id
    #
    # price    \
    #           -> amount
    # quantity /
    assert len(result.mappings) == 3

    actual_mappings = {
        (
            mapping.source_table_full_name,
            mapping.source_column_name,
            mapping.target_table_full_name,
            mapping.target_column_name,
            mapping.relation_type,
        )
        for mapping in result.mappings
    }

    assert actual_mappings == {
        (
            "ods.orders",
            "order_id",
            "dwd.order_summary",
            "order_id",
            "direct",
        ),
        (
            "ods.orders",
            "price",
            "dwd.order_summary",
            "amount",
            "transform",
        ),
        (
            "ods.orders",
            "quantity",
            "dwd.order_summary",
            "amount",
            "transform",
        ),
    }

    amount_mappings = [
        mapping
        for mapping in result.mappings
        if (
            mapping.target_column_name
            == "amount"
        )
    ]

    assert len(amount_mappings) == 2

    assert all(
        "price * quantity"
        in mapping.expression_text
        for mapping in amount_mappings
    )


# ============================================================
# 18. CTE计算字段必须有别名
# ============================================================

def test_reject_cte_expression_without_alias():
    sql_text = """
    WITH order_base AS (
        SELECT
            price * quantity
        FROM ods.orders
    )
    INSERT INTO dwd.order_summary (
        amount
    )
    SELECT
        amount
    FROM order_base;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="必须使用AS别名",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )


def test_extract_multiple_parallel_ctes():
    """
    测试两个相互独立的并列 CTE。

    order_base:
        ods.orders.order_id
            -> order_base.order_id

        ods.orders.price
        ods.orders.quantity
            -> order_base.amount

    customer_base:
        ods.customers.customer_name
            -> customer_base.customer_name

    最终：
        order_base.order_id
            -> dwd.order_detail.order_id

        customer_base.customer_name
            -> dwd.order_detail.customer_name

        order_base.amount
            -> dwd.order_detail.amount
    """

    sql_text = """
    WITH order_base AS (
        SELECT
            order_id,
            customer_id,
            price * quantity AS amount
        FROM ods.orders
    ),
    customer_base AS (
        SELECT
            customer_id,
            customer_name
        FROM ods.customers
    )
    INSERT INTO dwd.order_detail (
        order_id,
        customer_name,
        amount
    )
    SELECT
        o.order_id,
        c.customer_name,
        o.amount
    FROM order_base AS o
    JOIN customer_base AS c
      ON o.customer_id = c.customer_id;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert result.dialect == "hive"

    assert (
        result.target_table_full_name
        == "dwd.order_detail"
    )

    assert set(
        result.source_table_full_names
    ) == {
        "ods.orders",
        "ods.customers",
    }

    # order_id 对应一条；
    # customer_name 对应一条；
    # amount 对应 price 和 quantity 两条。
    assert len(result.mappings) == 4

    actual_mappings = {
        (
            item.ordinal_position,
            item.source_table_full_name,
            item.source_column_name,
            item.target_table_full_name,
            item.target_column_name,
            item.relation_type,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            1,
            "ods.orders",
            "order_id",
            "dwd.order_detail",
            "order_id",
            "direct",
        ),
        (
            2,
            "ods.customers",
            "customer_name",
            "dwd.order_detail",
            "customer_name",
            "direct",
        ),
        (
            3,
            "ods.orders",
            "price",
            "dwd.order_detail",
            "amount",
            "transform",
        ),
        (
            3,
            "ods.orders",
            "quantity",
            "dwd.order_detail",
            "amount",
            "transform",
        ),
    }

    amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name == "amount"
    ]

    assert len(amount_mappings) == 2

    assert all(
        item.expression_text
        == "price * quantity"
        for item in amount_mappings
    )

def test_reject_ambiguous_unqualified_column_from_ctes():
    """
    两个 CTE 都输出 id。

    最终 SELECT 如果只写：

        SELECT id

    系统不能猜测这个 id 来自 a 还是 b。
    """

    sql_text = """
    WITH a AS (
        SELECT order_id AS id
        FROM ods.orders
    ),
    b AS (
        SELECT customer_id AS id
        FROM ods.customers
    )
    INSERT INTO dwd.result_table (
        id
    )
    SELECT
        id
    FROM a
    JOIN b
      ON a.id = b.id;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="无法唯一确定",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )

def test_reject_dependent_ctes_for_now():
    """
    当前阶段只支持并列 CTE。

    暂时拒绝：

        second_cte
            ↓ 读取
        first_cte
    """

    sql_text = """
    WITH first_cte AS (
        SELECT
            order_id,
            amount
        FROM ods.orders
    ),
    second_cte AS (
        SELECT
            order_id,
            amount
        FROM first_cte
    )
    INSERT INTO dwd.orders (
        order_id,
        amount
    )
    SELECT
        order_id,
        amount
    FROM second_cte;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="暂不支持 CTE 依赖另一个 CTE",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )
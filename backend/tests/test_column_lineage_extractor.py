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
            == "source_order_id"
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
        item.expression_text
        == "price * quantity"
        for item in result.mappings
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
        == "SUM(amount)"
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
        == "COUNT(order_id)"
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

def test_extract_dependent_ctes():
    """
    测试 CTE 读取上一个 CTE。

    第一层：

        ods.orders
            ↓
        order_base

    第二层：

        order_base
            ↓
        order_enriched

    最终：

        order_enriched
            ↓
        dwd.order_summary

    必须穿透所有中间 CTE，
    返回真正的物理来源 ods.orders。
    """

    sql_text = """
    WITH order_base AS (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.orders
    ),
    order_enriched AS (
        SELECT
            order_id,
            amount
        FROM order_base
    )
    INSERT INTO dwd.order_summary (
        order_id,
        amount
    )
    SELECT
        order_id,
        amount
    FROM order_enriched;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert result.dialect == "hive"

    assert (
        result.target_table_full_name
        == "dwd.order_summary"
    )

    # 物理来源只能是 ods.orders。
    #
    # 中间 CTE 不能作为物理来源返回。
    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert "order_base" not in (
        result.source_table_full_names
    )

    assert "order_enriched" not in (
        result.source_table_full_names
    )

    # order_id 产生 1 条；
    # amount 产生 price、quantity 两条。
    assert len(result.mappings) == 3

    actual_mappings = {
        (
            item.ordinal_position,
            item.source_table_full_name,
            item.source_column_name,
            item.target_table_full_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            1,
            "ods.orders",
            "order_id",
            "dwd.order_summary",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            2,
            "ods.orders",
            "price",
            "dwd.order_summary",
            "amount",
            "transform",
            "price * quantity",
        ),
        (
            2,
            "ods.orders",
            "quantity",
            "dwd.order_summary",
            "amount",
            "transform",
            "price * quantity",
        ),
    }

def test_extract_three_level_cte_lineage():
    """
    测试三层 CTE 传递。

    ods.orders
        ↓
    level_one
        ↓
    level_two
        ↓
    level_three
        ↓
    dwd.order_result
    """

    sql_text = """
    WITH level_one AS (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.orders
    ),
    level_two AS (
        SELECT
            order_id,
            amount
        FROM level_one
    ),
    level_three AS (
        SELECT
            order_id,
            amount
        FROM level_two
    )
    INSERT INTO dwd.order_result (
        order_id,
        amount
    )
    SELECT
        order_id,
        amount
    FROM level_three;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert len(result.mappings) == 3

    amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "amount"
    ]

    assert len(amount_mappings) == 2

    assert {
        item.source_column_name
        for item in amount_mappings
    } == {
        "price",
        "quantity",
    }

    assert all(
        item.source_table_full_name
        == "ods.orders"
        for item in amount_mappings
    )

    assert all(
        item.relation_type == "transform"
        for item in amount_mappings
    )

    assert all(
        item.expression_text
        == "price * quantity"
        for item in amount_mappings
    )

def test_reject_missing_upstream_cte_column():
    """
    order_base 没有输出 missing_column。

    下游 CTE 不能凭空引用这个字段。
    """

    sql_text = """
    WITH order_base AS (
        SELECT
            order_id
        FROM ods.orders
    ),
    order_enriched AS (
        SELECT
            missing_column
        FROM order_base
    )
    INSERT INTO dwd.order_result (
        missing_column
    )
    SELECT
        missing_column
    FROM order_enriched;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match=(
            "CTE order_base "
            "没有输出字段：missing_column"
        ),
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )
def test_reject_cte_forward_reference():
    """
    first_cte 在 second_cte 定义之前，
    就引用了 second_cte。

    当前按照 SQL 定义顺序解析，
    必须拒绝前向引用。
    """

    sql_text = """
    WITH first_cte AS (
        SELECT
            order_id
        FROM second_cte
    ),
    second_cte AS (
        SELECT
            order_id
        FROM ods.orders
    )
    INSERT INTO dwd.order_result (
        order_id
    )
    SELECT
        order_id
    FROM first_cte;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="前向引用",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )

def test_transform_upstream_cte_column():
    """
    测试下游 CTE 对上游 CTE 字段继续计算。

    第一层：

        price * quantity AS amount

    第二层：

        amount * 1.1 AS tax_amount

    最终 tax_amount 的物理来源应该仍然是：

        ods.orders.price
        ods.orders.quantity

    不能把 order_base.amount 当成物理来源。
    """

    sql_text = """
    WITH order_base AS (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.orders
    ),
    taxed_order AS (
        SELECT
            order_id,
            amount * 1.1 AS tax_amount
        FROM order_base
    )
    INSERT INTO dwd.order_tax (
        order_id,
        tax_amount
    )
    SELECT
        order_id,
        tax_amount
    FROM taxed_order;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    # ========================================================
    # 1. 检查基础结果
    # ========================================================

    assert result.dialect == "hive"

    assert (
        result.target_table_full_name
        == "dwd.order_tax"
    )

    # 物理来源只能是 ods.orders。
    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert "order_base" not in (
        result.source_table_full_names
    )

    assert "taxed_order" not in (
        result.source_table_full_names
    )

    # ========================================================
    # 2. 检查血缘数量
    #
    # order_id:
    #     1 条
    #
    # tax_amount:
    #     price    -> tax_amount
    #     quantity -> tax_amount
    #     共 2 条
    #
    # 总共 3 条。
    # ========================================================

    assert len(result.mappings) == 3

    # ========================================================
    # 3. 检查 order_id
    # ========================================================

    order_id_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "order_id"
    ]

    assert len(order_id_mappings) == 1

    order_id_mapping = (
        order_id_mappings[0]
    )

    assert (
        order_id_mapping.source_table_full_name
        == "ods.orders"
    )

    assert (
        order_id_mapping.source_column_name
        == "order_id"
    )

    assert (
        order_id_mapping.target_table_full_name
        == "dwd.order_tax"
    )

    assert (
        order_id_mapping.relation_type
        == "direct"
    )

    assert (
        order_id_mapping.expression_text
        == "order_id"
    )

    # ========================================================
    # 4. 检查 tax_amount
    # ========================================================

    tax_amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "tax_amount"
    ]

    assert len(tax_amount_mappings) == 2

    assert {
        item.source_column_name
        for item in tax_amount_mappings
    } == {
        "price",
        "quantity",
    }

    assert all(
        item.source_table_full_name
        == "ods.orders"
        for item in tax_amount_mappings
    )

    assert all(
        item.target_table_full_name
        == "dwd.order_tax"
        for item in tax_amount_mappings
    )

    assert all(
        item.relation_type
        == "transform"
        for item in tax_amount_mappings
    )

    # 当前字段的直接计算表达式来自第二层 CTE：
    #
    #     amount * 1.1 AS tax_amount
    #
    # expression_text 不包含 AS tax_amount。
    assert all(
        item.expression_text
        == "amount * 1.1"
        for item in tax_amount_mappings
    )

    # ========================================================
    # 5. 检查完整映射
    # ========================================================

    actual_mappings = {
        (
            item.source_table_full_name,
            item.source_column_name,
            item.target_table_full_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            "ods.orders",
            "order_id",
            "dwd.order_tax",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            "ods.orders",
            "price",
            "dwd.order_tax",
            "tax_amount",
            "transform",
            "amount * 1.1",
        ),
        (
            "ods.orders",
            "quantity",
            "dwd.order_tax",
            "tax_amount",
            "transform",
            "amount * 1.1",
        ),
    }

def test_aggregate_upstream_cte_column():
    """
    测试下游 CTE 聚合上游 CTE 的计算字段。

    第一层：

        price * quantity AS amount

    第二层：

        SUM(amount) AS total_amount

    最终 total_amount 的物理来源应该是：

        ods.orders.price
        ods.orders.quantity

    最终关系类型应该是：

        aggregate
    """

    sql_text = """
    WITH order_base AS (
        SELECT
            customer_id,
            price * quantity AS amount
        FROM ods.orders
    ),
    customer_summary AS (
        SELECT
            customer_id,
            SUM(amount) AS total_amount
        FROM order_base
        GROUP BY customer_id
    )
    INSERT INTO ads.customer_summary (
        customer_id,
        total_amount
    )
    SELECT
        customer_id,
        total_amount
    FROM customer_summary;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    # ========================================================
    # 1. 检查基础信息
    # ========================================================

    assert result.dialect == "hive"

    assert (
        result.target_table_full_name
        == "ads.customer_summary"
    )

    # 中间 CTE 必须被展开成真正的物理来源表。
    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert "order_base" not in (
        result.source_table_full_names
    )

    assert "customer_summary" not in (
        result.source_table_full_names
    )

    # ========================================================
    # 2. 检查血缘数量
    #
    # customer_id:
    #     1 条
    #
    # total_amount:
    #     price    -> total_amount
    #     quantity -> total_amount
    #     共 2 条
    #
    # 总共 3 条。
    # ========================================================

    assert len(result.mappings) == 3

    # ========================================================
    # 3. 检查 customer_id
    # ========================================================

    customer_id_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "customer_id"
    ]

    assert len(customer_id_mappings) == 1

    customer_id_mapping = (
        customer_id_mappings[0]
    )

    assert (
        customer_id_mapping.source_table_full_name
        == "ods.orders"
    )

    assert (
        customer_id_mapping.source_column_name
        == "customer_id"
    )

    assert (
        customer_id_mapping.target_table_full_name
        == "ads.customer_summary"
    )

    assert (
        customer_id_mapping.relation_type
        == "direct"
    )

    assert (
        customer_id_mapping.expression_text
        == "customer_id"
    )

    # ========================================================
    # 4. 检查 total_amount
    # ========================================================

    total_amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "total_amount"
    ]

    assert len(total_amount_mappings) == 2

    # 最终物理来源仍然是第一层表达式中的
    # price 和 quantity。
    assert {
        item.source_column_name
        for item in total_amount_mappings
    } == {
        "price",
        "quantity",
    }

    assert all(
        item.source_table_full_name
        == "ods.orders"
        for item in total_amount_mappings
    )

    assert all(
        item.target_table_full_name
        == "ads.customer_summary"
        for item in total_amount_mappings
    )

    # 第二层使用了 SUM，因此最终关系类型必须升级为
    # aggregate，不能继续保留为 transform。
    assert all(
        item.relation_type
        == "aggregate"
        for item in total_amount_mappings
    )

    # 保存当前直接生成 total_amount 的源码表达式。
    assert all(
        item.expression_text
        == "SUM(amount)"
        for item in total_amount_mappings
    )

    # ========================================================
    # 5. 检查完整字段映射
    # ========================================================

    actual_mappings = {
        (
            item.ordinal_position,
            item.source_table_full_name,
            item.source_column_name,
            item.target_table_full_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            1,
            "ods.orders",
            "customer_id",
            "ads.customer_summary",
            "customer_id",
            "direct",
            "customer_id",
        ),
        (
            2,
            "ods.orders",
            "price",
            "ads.customer_summary",
            "total_amount",
            "aggregate",
            "SUM(amount)",
        ),
        (
            2,
            "ods.orders",
            "quantity",
            "ads.customer_summary",
            "total_amount",
            "aggregate",
            "SUM(amount)",
        ),
    }

def test_transform_columns_from_multiple_upstream_ctes():
    """
    测试一个下游 CTE 同时读取两个上游 CTE，
    并组合两边的字段。

    第一个上游 CTE：

        order_base.amount
        =
        ods.orders.price * ods.orders.quantity

    第二个上游 CTE：

        refund_base.refund_amount
        =
        ods.refunds.refund_amount

    下游 CTE：

        order_base.amount
        -
        refund_base.refund_amount
        =
        net_amount

    最终 net_amount 的物理来源应该是：

        ods.orders.price
        ods.orders.quantity
        ods.refunds.refund_amount
    """

    sql_text = """
    WITH order_base AS (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.orders
    ),
    refund_base AS (
        SELECT
            order_id,
            refund_amount
        FROM ods.refunds
    ),
    order_net AS (
        SELECT
            o.order_id,
            o.amount - r.refund_amount AS net_amount
        FROM order_base AS o
        JOIN refund_base AS r
          ON o.order_id = r.order_id
    )
    INSERT INTO ads.order_net (
        order_id,
        net_amount
    )
    SELECT
        order_id,
        net_amount
    FROM order_net;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    # ========================================================
    # 1. 检查基础信息
    # ========================================================

    assert result.dialect == "hive"

    assert (
        result.target_table_full_name
        == "ads.order_net"
    )

    # 最终来源必须是两张物理表。
    assert set(
        result.source_table_full_names
    ) == {
        "ods.orders",
        "ods.refunds",
    }

    # 所有中间 CTE 都不能作为物理来源返回。
    assert "order_base" not in (
        result.source_table_full_names
    )

    assert "refund_base" not in (
        result.source_table_full_names
    )

    assert "order_net" not in (
        result.source_table_full_names
    )

    # ========================================================
    # 2. 检查映射数量
    #
    # order_id：
    #
    #     ods.orders.order_id
    #         -> ads.order_net.order_id
    #
    # net_amount：
    #
    #     ods.orders.price
    #         -> ads.order_net.net_amount
    #
    #     ods.orders.quantity
    #         -> ads.order_net.net_amount
    #
    #     ods.refunds.refund_amount
    #         -> ads.order_net.net_amount
    #
    # 总共4条。
    # ========================================================

    assert len(result.mappings) == 4

    # ========================================================
    # 3. 检查 order_id
    # ========================================================

    order_id_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "order_id"
    ]

    assert len(order_id_mappings) == 1

    order_id_mapping = (
        order_id_mappings[0]
    )

    assert (
        order_id_mapping.source_table_full_name
        == "ods.orders"
    )

    assert (
        order_id_mapping.source_column_name
        == "order_id"
    )

    assert (
        order_id_mapping.target_table_full_name
        == "ads.order_net"
    )

    assert (
        order_id_mapping.relation_type
        == "direct"
    )

    assert (
        order_id_mapping.expression_text
        == "order_id"
    )

    # ========================================================
    # 4. 检查 net_amount
    # ========================================================

    net_amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "net_amount"
    ]

    assert len(net_amount_mappings) == 3

    actual_net_sources = {
        (
            item.source_table_full_name,
            item.source_column_name,
        )
        for item in net_amount_mappings
    }

    assert actual_net_sources == {
        (
            "ods.orders",
            "price",
        ),
        (
            "ods.orders",
            "quantity",
        ),
        (
            "ods.refunds",
            "refund_amount",
        ),
    }

    assert all(
        item.target_table_full_name
        == "ads.order_net"
        for item in net_amount_mappings
    )

    assert all(
        item.target_column_name
        == "net_amount"
        for item in net_amount_mappings
    )

    assert all(
        item.relation_type
        == "transform"
        for item in net_amount_mappings
    )

    # expression_text 保存下游 CTE 中真实出现的源码表达式。
    assert all(
        item.expression_text
        == "o.amount - r.refund_amount"
        for item in net_amount_mappings
    )

    # ========================================================
    # 5. 检查完整字段映射
    # ========================================================

    actual_mappings = {
        (
            item.ordinal_position,
            item.source_table_full_name,
            item.source_column_name,
            item.target_table_full_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            1,
            "ods.orders",
            "order_id",
            "ads.order_net",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            2,
            "ods.orders",
            "price",
            "ads.order_net",
            "net_amount",
            "transform",
            "o.amount - r.refund_amount",
        ),
        (
            2,
            "ods.orders",
            "quantity",
            "ads.order_net",
            "net_amount",
            "transform",
            "o.amount - r.refund_amount",
        ),
        (
            2,
            "ods.refunds",
            "refund_amount",
            "ads.order_net",
            "net_amount",
            "transform",
            "o.amount - r.refund_amount",
        ),
    }
def test_case_when_columns_from_multiple_upstream_ctes():
    """
    测试跨多个上游 CTE 的 CASE WHEN 字段血缘。

    订单金额：

        ods.orders.price
        ods.orders.quantity
            -> order_base.amount

    退款信息：

        ods.refunds.refund_status
        ods.refunds.refund_amount

    最终计算：

        CASE
            WHEN refund_status = 'approved'
            THEN amount - refund_amount
            ELSE amount
        END AS net_amount

    net_amount 应该依赖：

        ods.orders.price
        ods.orders.quantity
        ods.refunds.refund_status
        ods.refunds.refund_amount

    JOIN 条件中的 refunds.order_id 不应该成为
    net_amount 的来源字段。
    """

    sql_text = """
    WITH order_base AS (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.orders
    ),
    refund_base AS (
        SELECT
            order_id,
            refund_status,
            refund_amount
        FROM ods.refunds
    ),
    order_net AS (
        SELECT
            o.order_id,
            CASE
                WHEN r.refund_status = 'approved'
                THEN o.amount - r.refund_amount
                ELSE o.amount
            END AS net_amount
        FROM order_base AS o
        LEFT JOIN refund_base AS r
          ON o.order_id = r.order_id
    )
    INSERT INTO ads.order_net (
        order_id,
        net_amount
    )
    SELECT
        order_id,
        net_amount
    FROM order_net;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    # ========================================================
    # 1. 检查基础信息
    # ========================================================

    assert result.dialect == "hive"

    assert (
        result.target_table_full_name
        == "ads.order_net"
    )

    assert set(
        result.source_table_full_names
    ) == {
        "ods.orders",
        "ods.refunds",
    }

    # CTE 名称不能作为物理来源返回。
    assert "order_base" not in (
        result.source_table_full_names
    )

    assert "refund_base" not in (
        result.source_table_full_names
    )

    assert "order_net" not in (
        result.source_table_full_names
    )

    # ========================================================
    # 2. 检查血缘数量
    #
    # order_id：
    #     1条
    #
    # net_amount：
    #     price
    #     quantity
    #     refund_status
    #     refund_amount
    #     共4条
    #
    # 总共5条。
    # ========================================================

    assert len(result.mappings) == 5

    # ========================================================
    # 3. 检查 order_id
    # ========================================================

    order_id_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "order_id"
    ]

    assert len(order_id_mappings) == 1

    order_id_mapping = (
        order_id_mappings[0]
    )

    assert (
        order_id_mapping.source_table_full_name
        == "ods.orders"
    )

    assert (
        order_id_mapping.source_column_name
        == "order_id"
    )

    assert (
        order_id_mapping.relation_type
        == "direct"
    )

    assert (
        order_id_mapping.expression_text
        == "order_id"
    )

    # ========================================================
    # 4. 检查 net_amount
    # ========================================================

    net_amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "net_amount"
    ]

    assert len(net_amount_mappings) == 4

    actual_net_sources = {
        (
            item.source_table_full_name,
            item.source_column_name,
        )
        for item in net_amount_mappings
    }

    assert actual_net_sources == {
        (
            "ods.orders",
            "price",
        ),
        (
            "ods.orders",
            "quantity",
        ),
        (
            "ods.refunds",
            "refund_status",
        ),
        (
            "ods.refunds",
            "refund_amount",
        ),
    }

    # JOIN 条件中的 refunds.order_id
    # 不能错误地加入 net_amount 的字段血缘。
    assert (
        "ods.refunds",
        "order_id",
    ) not in actual_net_sources

    assert all(
        item.target_table_full_name
        == "ads.order_net"
        for item in net_amount_mappings
    )

    assert all(
        item.relation_type
        == "transform"
        for item in net_amount_mappings
    )

    # ========================================================
    # 5. 检查 CASE 表达式
    #
    # SQLGlot 会把多行 CASE 格式化为一行。
    # ========================================================

    expected_expression = (
        "CASE "
        "WHEN r.refund_status = 'approved' "
        "THEN o.amount - r.refund_amount "
        "ELSE o.amount "
        "END"
    )

    assert all(
        item.expression_text
        == expected_expression
        for item in net_amount_mappings
    )

    # ========================================================
    # 6. 检查完整映射
    # ========================================================

    actual_mappings = {
        (
            item.ordinal_position,
            item.source_table_full_name,
            item.source_column_name,
            item.target_table_full_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            1,
            "ods.orders",
            "order_id",
            "ads.order_net",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            2,
            "ods.orders",
            "price",
            "ads.order_net",
            "net_amount",
            "transform",
            expected_expression,
        ),
        (
            2,
            "ods.orders",
            "quantity",
            "ads.order_net",
            "net_amount",
            "transform",
            expected_expression,
        ),
        (
            2,
            "ods.refunds",
            "refund_status",
            "ads.order_net",
            "net_amount",
            "transform",
            expected_expression,
        ),
        (
            2,
            "ods.refunds",
            "refund_amount",
            "ads.order_net",
            "net_amount",
            "transform",
            expected_expression,
        ),
    }

def test_extract_union_all_lineage():
    """
    测试两个物理来源表通过 UNION ALL
    写入同一个目标表。

    web_orders.amount：

        price * quantity

    store_orders.amount：

        amount
    """

    sql_text = """
    INSERT INTO dwd.all_orders (
        order_id,
        amount
    )
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.web_orders

    UNION ALL

    SELECT
        order_id,
        amount
    FROM ods.store_orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert result.dialect == "hive"

    assert (
        result.target_table_full_name
        == "dwd.all_orders"
    )

    assert set(
        result.source_table_full_names
    ) == {
        "ods.web_orders",
        "ods.store_orders",
    }

    # web_orders:
    #
    #     order_id 一条
    #     price、quantity 两条
    #
    # store_orders:
    #
    #     order_id 一条
    #     amount 一条
    #
    # 总共5条。
    assert len(result.mappings) == 5

    actual_mappings = {
        (
            item.ordinal_position,
            item.source_table_full_name,
            item.source_column_name,
            item.target_table_full_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            1,
            "ods.web_orders",
            "order_id",
            "dwd.all_orders",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            2,
            "ods.web_orders",
            "price",
            "dwd.all_orders",
            "amount",
            "transform",
            "price * quantity",
        ),
        (
            2,
            "ods.web_orders",
            "quantity",
            "dwd.all_orders",
            "amount",
            "transform",
            "price * quantity",
        ),
        (
            1,
            "ods.store_orders",
            "order_id",
            "dwd.all_orders",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            2,
            "ods.store_orders",
            "amount",
            "dwd.all_orders",
            "amount",
            "direct",
            "amount",
        ),
    }

def test_extract_three_union_all_branches():
    """
    验证递归展开三个 UNION ALL 分支。
    """

    sql_text = """
    INSERT INTO dwd.all_users (
        user_id
    )
    SELECT
        user_id
    FROM ods.web_users

    UNION ALL

    SELECT
        user_id
    FROM ods.app_users

    UNION ALL

    SELECT
        user_id
    FROM ods.store_users;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert set(
        result.source_table_full_names
    ) == {
        "ods.web_users",
        "ods.app_users",
        "ods.store_users",
    }

    assert len(result.mappings) == 3

    actual_sources = {
        (
            item.source_table_full_name,
            item.source_column_name,
        )
        for item in result.mappings
    }

    assert actual_sources == {
        (
            "ods.web_users",
            "user_id",
        ),
        (
            "ods.app_users",
            "user_id",
        ),
        (
            "ods.store_users",
            "user_id",
        ),
    }

    assert all(
        item.target_table_full_name
        == "dwd.all_users"
        for item in result.mappings
    )

    assert all(
        item.target_column_name
        == "user_id"
        for item in result.mappings
    )

    assert all(
        item.relation_type == "direct"
        for item in result.mappings
    )

def test_reject_union_distinct():
    """
    当前阶段只支持 UNION ALL。

    普通 UNION 会执行去重，
    它比 UNION ALL 多了一层集合处理语义，
    暂时明确拒绝。
    """

    sql_text = """
    INSERT INTO dwd.all_users (
        user_id
    )
    SELECT
        user_id
    FROM ods.web_users

    UNION

    SELECT
        user_id
    FROM ods.app_users;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="只支持 UNION ALL",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )

def test_reject_union_all_column_count_mismatch():
    """
    UNION ALL 每个分支的字段数量必须与
    INSERT 目标字段数量一致。
    """

    sql_text = """
    INSERT INTO dwd.all_orders (
        order_id,
        amount
    )
    SELECT
        order_id,
        amount
    FROM ods.web_orders

    UNION ALL

    SELECT
        order_id
    FROM ods.store_orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="字段数量",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )

def test_extract_cte_union_all_lineage():
    """
    测试两个 CTE 通过 UNION ALL
    写入同一个物理目标表。
    """

    sql_text = """
    WITH web_base AS (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.web_orders
    ),
    store_base AS (
        SELECT
            order_id,
            amount
        FROM ods.store_orders
    )
    INSERT INTO dwd.all_orders (
        order_id,
        amount
    )
    SELECT
        order_id,
        amount
    FROM web_base

    UNION ALL

    SELECT
        order_id,
        amount
    FROM store_base;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert result.dialect == "hive"

    assert (
        result.target_table_full_name
        == "dwd.all_orders"
    )

    # 只能返回真正的物理来源表。
    assert set(
        result.source_table_full_names
    ) == {
        "ods.web_orders",
        "ods.store_orders",
    }

    assert "web_base" not in (
        result.source_table_full_names
    )

    assert "store_base" not in (
        result.source_table_full_names
    )

    assert len(result.mappings) == 5

    actual_mappings = {
        (
            item.ordinal_position,
            item.source_table_full_name,
            item.source_column_name,
            item.target_table_full_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            1,
            "ods.web_orders",
            "order_id",
            "dwd.all_orders",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            2,
            "ods.web_orders",
            "price",
            "dwd.all_orders",
            "amount",
            "transform",
            "price * quantity",
        ),
        (
            2,
            "ods.web_orders",
            "quantity",
            "dwd.all_orders",
            "amount",
            "transform",
            "price * quantity",
        ),
        (
            1,
            "ods.store_orders",
            "order_id",
            "dwd.all_orders",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            2,
            "ods.store_orders",
            "amount",
            "dwd.all_orders",
            "amount",
            "direct",
            "amount",
        ),
    }
def test_extract_dependent_cte_union_all_lineage():
    """
    验证 UNION ALL 使用的 CTE 还可以继续依赖前一个 CTE。
    """

    sql_text = """
    WITH web_raw AS (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.web_orders
    ),
    web_base AS (
        SELECT
            order_id,
            amount
        FROM web_raw
    ),
    store_base AS (
        SELECT
            order_id,
            amount
        FROM ods.store_orders
    )
    INSERT INTO dwd.all_orders (
        order_id,
        amount
    )
    SELECT
        order_id,
        amount
    FROM web_base

    UNION ALL

    SELECT
        order_id,
        amount
    FROM store_base;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert set(
        result.source_table_full_names
    ) == {
        "ods.web_orders",
        "ods.store_orders",
    }

    assert len(result.mappings) == 5

    web_amount_mappings = [
        item
        for item in result.mappings
        if (
            item.source_table_full_name
            == "ods.web_orders"
            and item.target_column_name
            == "amount"
        )
    ]

    assert len(web_amount_mappings) == 2

    assert {
        item.source_column_name
        for item in web_amount_mappings
    } == {
        "price",
        "quantity",
    }

    assert all(
        item.relation_type == "transform"
        for item in web_amount_mappings
    )

    assert all(
        item.expression_text
        == "price * quantity"
        for item in web_amount_mappings
    )
def test_extract_from_subquery_lineage():
    sql_text = """
    INSERT INTO dwd.order_detail (
        order_id,
        amount
    )
    SELECT
        s.order_id,
        s.amount
    FROM (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.orders
    ) AS s;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert (
        result.target_table_full_name
        == "dwd.order_detail"
    )

    assert len(result.mappings) == 3

    actual_mappings = {
        (
            item.source_table_full_name,
            item.source_column_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            "ods.orders",
            "order_id",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            "ods.orders",
            "price",
            "amount",
            "transform",
            "price * quantity",
        ),
        (
            "ods.orders",
            "quantity",
            "amount",
            "transform",
            "price * quantity",
        ),
    }
def test_extract_nested_subquery_lineage():
    sql_text = """
    INSERT INTO dwd.order_detail (
        order_id,
        amount
    )
    SELECT
        level_two.order_id,
        level_two.amount
    FROM (
        SELECT
            level_one.order_id,
            level_one.amount
        FROM (
            SELECT
                order_id,
                price * quantity AS amount
            FROM ods.orders
        ) AS level_one
    ) AS level_two;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert len(result.mappings) == 3

    amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "amount"
    ]

    assert len(amount_mappings) == 2

    assert {
        item.source_column_name
        for item in amount_mappings
    } == {
        "price",
        "quantity",
    }

    assert all(
        item.relation_type == "transform"
        for item in amount_mappings
    )

    assert all(
        item.expression_text
        == "price * quantity"
        for item in amount_mappings
    )
def test_transform_subquery_output_column():
    sql_text = """
    INSERT INTO dwd.order_tax (
        order_id,
        tax_amount
    )
    SELECT
        s.order_id,
        s.amount * 1.1 AS tax_amount
    FROM (
        SELECT
            order_id,
            price * quantity AS amount
        FROM ods.orders
    ) AS s;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert len(result.mappings) == 3

    tax_amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "tax_amount"
    ]

    assert len(tax_amount_mappings) == 2

    assert {
        item.source_column_name
        for item in tax_amount_mappings
    } == {
        "price",
        "quantity",
    }

    assert all(
        item.relation_type == "transform"
        for item in tax_amount_mappings
    )

    assert all(
        item.expression_text
        == "s.amount * 1.1"
        for item in tax_amount_mappings
    )
def test_reject_subquery_without_alias():
    sql_text = """
    INSERT INTO dwd.order_detail (
        order_id
    )
    SELECT
        order_id
    FROM (
        SELECT
            order_id
        FROM ods.orders
    );
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="子查询必须定义别名",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )

def test_extract_create_table_as_select():
    sql_text = """
    CREATE TABLE dwd.order_detail AS
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert (
        result.target_table_full_name
        == "dwd.order_detail"
    )

    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert len(result.mappings) == 3

    actual_mappings = {
        (
            item.source_table_full_name,
            item.source_column_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            "ods.orders",
            "order_id",
            "order_id",
            "direct",
            "order_id",
        ),
        (
            "ods.orders",
            "price",
            "amount",
            "transform",
            "price * quantity",
        ),
        (
            "ods.orders",
            "quantity",
            "amount",
            "transform",
            "price * quantity",
        ),
    }
def test_extract_create_view_as_select():
    sql_text = """
    CREATE VIEW ads.customer_summary AS
    SELECT
        customer_id,
        SUM(amount) AS total_amount
    FROM dwd.orders
    GROUP BY customer_id;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert (
        result.target_table_full_name
        == "ads.customer_summary"
    )

    assert result.source_table_full_names == (
        "dwd.orders",
    )

    assert len(result.mappings) == 2

    actual_mappings = {
        (
            item.source_column_name,
            item.target_column_name,
            item.relation_type,
            item.expression_text,
        )
        for item in result.mappings
    }

    assert actual_mappings == {
        (
            "customer_id",
            "customer_id",
            "direct",
            "customer_id",
        ),
        (
            "amount",
            "total_amount",
            "aggregate",
            "SUM(amount)",
        ),
    }
def test_extract_create_table_as_union_all():
    sql_text = """
    CREATE TABLE dwd.all_orders AS
    SELECT
        order_id,
        amount
    FROM ods.web_orders

    UNION ALL

    SELECT
        order_id,
        amount
    FROM ods.store_orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert set(
        result.source_table_full_names
    ) == {
        "ods.web_orders",
        "ods.store_orders",
    }

    assert (
        result.target_table_full_name
        == "dwd.all_orders"
    )

    assert len(result.mappings) == 4

    assert {
        (
            item.source_table_full_name,
            item.source_column_name,
            item.target_column_name,
        )
        for item in result.mappings
    } == {
        (
            "ods.web_orders",
            "order_id",
            "order_id",
        ),
        (
            "ods.web_orders",
            "amount",
            "amount",
        ),
        (
            "ods.store_orders",
            "order_id",
            "order_id",
        ),
        (
            "ods.store_orders",
            "amount",
            "amount",
        ),
    }
def test_extract_create_view_as_cte():
    sql_text = """
    CREATE VIEW ads.order_summary AS
    WITH order_base AS (
        SELECT
            customer_id,
            price * quantity AS amount
        FROM ods.orders
    )
    SELECT
        customer_id,
        SUM(amount) AS total_amount
    FROM order_base
    GROUP BY customer_id;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert (
        result.target_table_full_name
        == "ads.order_summary"
    )

    assert result.source_table_full_names == (
        "ods.orders",
    )

    assert len(result.mappings) == 3

    total_amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "total_amount"
    ]

    assert len(total_amount_mappings) == 2

    assert {
        item.source_column_name
        for item in total_amount_mappings
    } == {
        "price",
        "quantity",
    }

    assert all(
        item.relation_type == "aggregate"
        for item in total_amount_mappings
    )

    assert all(
        item.expression_text
        == "SUM(amount)"
        for item in total_amount_mappings
    )
def test_reject_create_expression_without_alias():
    sql_text = """
    CREATE TABLE dwd.order_detail AS
    SELECT
        order_id,
        price * quantity
    FROM ods.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="别名",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )

import pytest

from app.services.column_lineage_extractor import (
    DirectColumnLineageResult,
    UnsupportedColumnLineageError,
    extract_direct_column_lineage,
)


def assert_order_detail_lineage(
    result: DirectColumnLineageResult,
    *,
    expected_dialect: str,
    source_table: str,
    target_table: str,
) -> None:
    """
    验证三种 SQL 方言共同的字段血缘结果。

    预期：

        source.order_id
            -> target.order_id

        source.price
            -> target.amount

        source.quantity
            -> target.amount
    """

    assert result.dialect == expected_dialect

    assert (
        result.target_table_full_name
        == target_table
    )

    assert result.source_table_full_names == (
        source_table,
    )

    assert len(result.mappings) == 3

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
            source_table,
            "order_id",
            target_table,
            "order_id",
            "direct",
            "order_id",
        ),
        (
            source_table,
            "price",
            target_table,
            "amount",
            "transform",
            "price * quantity",
        ),
        (
            source_table,
            "quantity",
            target_table,
            "amount",
            "transform",
            "price * quantity",
        ),
    }


def test_hive_insert_overwrite_lineage():
    """
    测试 Hive INSERT OVERWRITE TABLE。

    Hive 常见写法：

        INSERT OVERWRITE TABLE ...
    """

    sql_text = """
    INSERT OVERWRITE TABLE dwd.order_detail (
        order_id,
        amount
    )
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert_order_detail_lineage(
        result,
        expected_dialect="hive",
        source_table="ods.orders",
        target_table="dwd.order_detail",
    )
def test_reject_hive_insert_overwrite_without_target_columns():
    """
    Hive INSERT OVERWRITE 没有显式声明目标字段时，
    纯 SQL 提取器不能推测目标表字段顺序。

    因此必须明确拒绝，而不是生成可能错误的血缘。
    """

    sql_text = """
    INSERT OVERWRITE TABLE dwd.order_detail
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="必须显式声明目标字段列表",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="hive",
        )

def test_hive_insert_overwrite_with_target_columns():
    """
    测试带目标字段列表的 Hive INSERT OVERWRITE。

    某些 Hive/Spark 环境允许：

        INSERT OVERWRITE TABLE table_name (
            column_a,
            column_b
        )
        SELECT ...
    """

    sql_text = """
    INSERT OVERWRITE TABLE dwd.order_detail (
        order_id,
        amount
    )
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="hive",
    )

    assert_order_detail_lineage(
        result,
        expected_dialect="hive",
        source_table="ods.orders",
        target_table="dwd.order_detail",
    )
def test_reject_spark_insert_overwrite_without_target_columns():
    """
    Spark INSERT OVERWRITE 没有显式目标字段时，
    同样不能只根据 SELECT 猜测目标表字段。
    """

    sql_text = """
    INSERT OVERWRITE TABLE dwd.order_detail
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.orders;
    """

    with pytest.raises(
        UnsupportedColumnLineageError,
        match="必须显式声明目标字段列表",
    ):
        extract_direct_column_lineage(
            sql_text=sql_text,
            dialect="spark",
        )

def test_spark_insert_overwrite_with_target_columns():
    """
    Spark INSERT OVERWRITE 显式声明目标字段时，
    可以安全建立字段位置映射。
    """

    sql_text = """
    INSERT OVERWRITE TABLE dwd.order_detail (
        order_id,
        amount
    )
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="spark",
    )

    assert_order_detail_lineage(
        result,
        expected_dialect="spark",
        source_table="ods.orders",
        target_table="dwd.order_detail",
    )

def test_spark_insert_overwrite_lineage():
    """
    测试 Spark SQL INSERT OVERWRITE TABLE。
    """

    sql_text = """
    INSERT OVERWRITE TABLE dwd.order_detail (
        order_id,
        amount
    )
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="spark",
    )

    assert_order_detail_lineage(
        result,
        expected_dialect="spark",
        source_table="ods.orders",
        target_table="dwd.order_detail",
    )


def test_spark_create_table_as_select():
    """
    测试 Spark SQL CREATE TABLE AS SELECT。
    """

    sql_text = """
    CREATE TABLE dwd.order_detail
    USING PARQUET
    AS
    SELECT
        order_id,
        price * quantity AS amount
    FROM ods.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="spark",
    )

    assert_order_detail_lineage(
        result,
        expected_dialect="spark",
        source_table="ods.orders",
        target_table="dwd.order_detail",
    )


def test_postgres_insert_select_lineage():
    """
    测试 PostgreSQL INSERT INTO ... SELECT。
    """

    sql_text = """
    INSERT INTO analytics.order_detail (
        order_id,
        amount
    )
    SELECT
        order_id,
        price * quantity AS amount
    FROM public.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="postgres",
    )

    assert_order_detail_lineage(
        result,
        expected_dialect="postgres",
        source_table="public.orders",
        target_table="analytics.order_detail",
    )


def test_postgres_create_table_as_select():
    """
    测试 PostgreSQL CREATE TABLE AS SELECT。
    """

    sql_text = """
    CREATE TABLE analytics.order_detail AS
    SELECT
        order_id,
        price * quantity AS amount
    FROM public.orders;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="postgres",
    )

    assert_order_detail_lineage(
        result,
        expected_dialect="postgres",
        source_table="public.orders",
        target_table="analytics.order_detail",
    )


def test_postgres_create_or_replace_view():
    """
    测试 PostgreSQL CREATE OR REPLACE VIEW。
    """

    sql_text = """
    CREATE OR REPLACE VIEW analytics.customer_summary AS
    SELECT
        customer_id,
        SUM(amount) AS total_amount
    FROM public.orders
    GROUP BY customer_id;
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect="postgres",
    )

    assert result.dialect == "postgres"

    assert (
        result.target_table_full_name
        == "analytics.customer_summary"
    )

    assert result.source_table_full_names == (
        "public.orders",
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


@pytest.mark.parametrize(
    (
        "dialect",
        "source_table",
        "target_table",
    ),
    [
        (
            "hive",
            "ods.orders",
            "dwd.order_detail",
        ),
        (
            "spark",
            "ods.orders",
            "dwd.order_detail",
        ),
        (
            "postgres",
            "public.orders",
            "analytics.order_detail",
        ),
    ],
)
def test_case_when_across_dialects(
    dialect: str,
    source_table: str,
    target_table: str,
):
    """
    使用参数化测试验证三种方言的 CASE WHEN。

    一个测试函数会实际产生3个测试用例。
    """

    sql_text = f"""
    INSERT INTO {target_table} (
        order_id,
        final_amount
    )
    SELECT
        order_id,
        CASE
            WHEN status = 'paid'
            THEN amount
            ELSE 0
        END AS final_amount
    FROM {source_table};
    """

    result = extract_direct_column_lineage(
        sql_text=sql_text,
        dialect=dialect,
    )

    assert result.dialect == dialect

    assert (
        result.target_table_full_name
        == target_table
    )

    assert result.source_table_full_names == (
        source_table,
    )

    assert len(result.mappings) == 3

    final_amount_mappings = [
        item
        for item in result.mappings
        if item.target_column_name
        == "final_amount"
    ]

    assert len(final_amount_mappings) == 2

    assert {
        item.source_column_name
        for item in final_amount_mappings
    } == {
        "status",
        "amount",
    }

    assert all(
        item.relation_type == "transform"
        for item in final_amount_mappings
    )

    expected_expression = (
        "CASE "
        "WHEN status = 'paid' "
        "THEN amount "
        "ELSE 0 "
        "END"
    )

    assert all(
        item.expression_text
        == expected_expression
        for item in final_amount_mappings
    )
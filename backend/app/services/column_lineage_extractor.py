from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError


# ============================================================
# 1. 自定义异常
# ============================================================

class UnsupportedColumnLineageError(ValueError):
    """
    当前 SQL 结构暂时不支持字段级血缘解析。

    使用单独的异常类型，可以区分：

    1. SQL 本身存在语法错误；
    2. SQL 语法正确，但当前版本还不支持这种结构。
    """


# ============================================================
# 2. 单条字段映射
# ============================================================

@dataclass(frozen=True)
class DirectColumnMapping:
    """
    一条最基础的直接字段映射。

    例如：

        INSERT INTO dwd.orders (order_id)
        SELECT order_id
        FROM ods.orders;

    会得到：

        source_table_full_name = ods.orders
        source_column_name     = order_id
        target_table_full_name = dwd.orders
        target_column_name     = order_id
    """

    ordinal_position: int

    source_table_full_name: str
    source_column_name: str

    target_table_full_name: str
    target_column_name: str

    expression_text: str

    relation_type: str = "direct"


# ============================================================
# 3. 整条 SQL 的字段映射结果
# ============================================================

@dataclass(frozen=True)
class DirectColumnLineageResult:
    """
    一条 INSERT ... SELECT SQL 的字段血缘结果。
    """

    dialect: str

    source_table_full_name: str
    target_table_full_name: str

    mappings: tuple[
        DirectColumnMapping,
        ...
    ]


# ============================================================
# 4. 表名处理
# ============================================================

def _table_full_name(
    table_expression: exp.Table,
) -> str:
    """
    将 SQLGlot 的 Table 节点转换成完整表名。

    支持：

        orders
        ods.orders
        catalog.ods.orders
    """

    table_name = table_expression.name

    if not table_name:
        raise UnsupportedColumnLineageError(
            "SQL 中的数据表名称为空"
        )

    parts: list[str] = []

    catalog_name = table_expression.catalog
    schema_name = table_expression.db

    if catalog_name:
        parts.append(catalog_name)

    if schema_name:
        parts.append(schema_name)

    parts.append(table_name)

    return ".".join(parts)


# ============================================================
# 5. 解析目标表和目标字段
# ============================================================

def _extract_insert_target(
    insert_expression: exp.Insert,
) -> tuple[
    exp.Table,
    tuple[str, ...],
]:
    """
    从 INSERT 节点中提取：

        目标表
        目标字段列表

    当前阶段要求 INSERT 必须显式写出目标字段。

    支持：

        INSERT INTO dwd.orders (
            order_id,
            amount
        )
        SELECT ...

    暂不支持：

        INSERT INTO dwd.orders
        SELECT ...

    因为第二种写法必须依赖数据库表结构，
    才能知道 SELECT 第一个字段对应哪个目标字段。
    """

    target_expression = (
        insert_expression.this
    )

    if not isinstance(
        target_expression,
        exp.Schema,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段要求 INSERT 必须显式声明目标字段列表，"
            "例如：INSERT INTO dwd.orders "
            "(order_id, amount) SELECT ..."
        )

    target_table_expression = (
        target_expression.this
    )

    if not isinstance(
        target_table_expression,
        exp.Table,
    ):
        raise UnsupportedColumnLineageError(
            "无法识别 INSERT 的目标数据表"
        )

    target_column_names: list[str] = []

    for column_expression in (
        target_expression.expressions
    ):
        column_name = column_expression.name

        if not column_name:
            raise UnsupportedColumnLineageError(
                "INSERT 目标字段名称为空"
            )

        target_column_names.append(
            column_name
        )

    if not target_column_names:
        raise UnsupportedColumnLineageError(
            "INSERT 没有声明目标字段"
        )

    return (
        target_table_expression,
        tuple(target_column_names),
    )


# ============================================================
# 6. 提取 SELECT
# ============================================================

def _extract_select(
    insert_expression: exp.Insert,
) -> exp.Select:
    """
    取得 INSERT 后面的 SELECT。

    当前阶段只支持一个普通 SELECT，
    暂不支持 UNION 等复合查询。
    """

    query_expression = (
        insert_expression.expression
    )

    if not isinstance(
        query_expression,
        exp.Select,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段只支持 INSERT INTO ... SELECT ..."
        )

    return query_expression


# ============================================================
# 7. 检查暂不支持的复杂查询
# ============================================================

def _validate_simple_select(
    select_expression: exp.Select,
) -> None:
    """
    确认 SELECT 是当前阶段支持的简单结构。

    当前阶段不允许：

        JOIN
        CTE
        子查询
    """

    joins = (
        select_expression.args.get("joins")
        or []
    )

    if joins:
        raise UnsupportedColumnLineageError(
            "当前阶段暂不支持 JOIN 字段血缘"
        )

    cte_expression = next(
        select_expression.find_all(exp.CTE),
        None,
    )

    if cte_expression is not None:
        raise UnsupportedColumnLineageError(
            "当前阶段暂不支持 CTE 字段血缘"
        )

    subquery_expression = next(
        select_expression.find_all(
            exp.Subquery
        ),
        None,
    )

    if subquery_expression is not None:
        raise UnsupportedColumnLineageError(
            "当前阶段暂不支持子查询字段血缘"
        )


# ============================================================
# 8. 提取唯一来源表
# ============================================================

def _extract_single_source_table(
    select_expression: exp.Select,
) -> exp.Table:
    """
    从 SELECT 中取得唯一来源表。

    当前阶段只允许一个物理来源表。
    """

    source_tables = list(
        select_expression.find_all(
            exp.Table
        )
    )

    if not source_tables:
        raise UnsupportedColumnLineageError(
            "SELECT 中没有找到来源数据表"
        )

    unique_source_tables: dict[
        str,
        exp.Table,
    ] = {}

    for table_expression in source_tables:
        full_name = _table_full_name(
            table_expression
        )

        unique_source_tables[
            full_name
        ] = table_expression

    if len(unique_source_tables) != 1:
        table_names = ", ".join(
            sorted(
                unique_source_tables.keys()
            )
        )

        raise UnsupportedColumnLineageError(
            "当前阶段只支持一个来源表，"
            f"实际识别到：{table_names}"
        )

    return next(
        iter(
            unique_source_tables.values()
        )
    )


# ============================================================
# 9. 提取投影字段中的直接来源字段
# ============================================================

def _extract_direct_source_column(
    projection_expression: exp.Expression,
    dialect: str,
) -> tuple[
    str,
    str,
]:
    """
    从一个 SELECT 投影字段中提取直接来源字段。

    支持：

        order_id

        source_order_id AS order_id

        o.order_id

    不支持：

        *

        price * quantity

        SUM(amount)

        CASE WHEN ...

        1 AS constant_value
    """

    expression_text = (
        projection_expression.sql(
            dialect=dialect,
        )
    )

    # 如果字段写了 AS 别名：
    #
    # source_order_id AS order_id
    #
    # SQLGlot 外层是 Alias，
    # 真正的来源表达式在 .this 中。
    if isinstance(
        projection_expression,
        exp.Alias,
    ):
        source_expression = (
            projection_expression.this
        )
    else:
        source_expression = (
            projection_expression
        )

    # SELECT * 可能直接解析成 Star。
    if isinstance(
        source_expression,
        exp.Star,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段暂不支持 SELECT *"
        )

    # table.* 可能解析成 Column(Star)。
    if (
        isinstance(
            source_expression,
            exp.Column,
        )
        and isinstance(
            source_expression.this,
            exp.Star,
        )
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段暂不支持 table.*"
        )

    if not isinstance(
        source_expression,
        exp.Column,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段只支持直接字段映射，"
            f"暂不支持表达式：{expression_text}"
        )

    source_column_name = (
        source_expression.name
    )

    if not source_column_name:
        raise UnsupportedColumnLineageError(
            "无法识别来源字段名称："
            f"{expression_text}"
        )

    return (
        source_column_name,
        expression_text,
    )


# ============================================================
# 10. 正式公开函数
# ============================================================

def extract_direct_column_lineage(
    sql_text: str,
    dialect: str,
) -> DirectColumnLineageResult:
    """
    从一条简单的 INSERT INTO ... SELECT ... SQL 中，
    提取直接字段血缘。

    当前阶段按照字段位置进行匹配：

        INSERT 第 1 个目标字段
            对应
        SELECT 第 1 个投影字段

        INSERT 第 2 个目标字段
            对应
        SELECT 第 2 个投影字段

    例如：

        INSERT INTO dwd.orders (
            order_id,
            customer_id
        )
        SELECT
            source_order_id,
            source_customer_id
        FROM ods.orders;

    得到：

        ods.orders.source_order_id
            -> dwd.orders.order_id

        ods.orders.source_customer_id
            -> dwd.orders.customer_id
    """

    if not sql_text.strip():
        raise ValueError(
            "sql_text 不能为空"
        )

    normalized_dialect = (
        dialect.strip().lower()
    )

    if not normalized_dialect:
        raise ValueError(
            "dialect 不能为空"
        )

    # unknown 不能直接作为 SQLGlot read 方言。
    read_dialect = (
        None
        if normalized_dialect == "unknown"
        else normalized_dialect
    )

    try:
        expression = sqlglot.parse_one(
            sql_text,
            read=read_dialect,
        )

    except ParseError as exc:
        raise ValueError(
            f"SQL 解析失败：{exc}"
        ) from exc

    if not isinstance(
        expression,
        exp.Insert,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段只支持 INSERT INTO ... SELECT ..."
        )

    (
        target_table_expression,
        target_column_names,
    ) = _extract_insert_target(
        expression
    )

    select_expression = _extract_select(
        expression
    )

    _validate_simple_select(
        select_expression
    )

    source_table_expression = (
        _extract_single_source_table(
            select_expression
        )
    )

    source_table_full_name = (
        _table_full_name(
            source_table_expression
        )
    )

    target_table_full_name = (
        _table_full_name(
            target_table_expression
        )
    )

    projection_expressions = tuple(
        select_expression.expressions
    )

    # --------------------------------------------------------
    # 先验证所有 SELECT 投影字段
    # --------------------------------------------------------
    #
    # 这里必须在字段数量检查之前执行。
    #
    # 例如：
    #
    #     INSERT INTO target_table (
    #         column_a,
    #         column_b
    #     )
    #     SELECT *
    #     FROM source_table;
    #
    # INSERT 有两个目标字段，
    # 但是 SQLGlot 会把 * 识别成一个投影表达式。
    #
    # 如果先比较数量，就只会得到：
    #
    #     目标字段 2 个，SELECT 字段 1 个
    #
    # 而真正应该告诉调用者的是：
    #
    #     当前阶段暂不支持 SELECT *
    # --------------------------------------------------------

    parsed_projections: list[
        tuple[str, str]
    ] = []

    for projection_expression in (
        projection_expressions
    ):
        (
            source_column_name,
            expression_text,
        ) = _extract_direct_source_column(
            projection_expression=(
                projection_expression
            ),
            dialect=normalized_dialect,
        )

        parsed_projections.append(
            (
                source_column_name,
                expression_text,
            )
        )

    # --------------------------------------------------------
    # 所有投影字段验证通过以后，再检查字段数量
    # --------------------------------------------------------

    if (
        len(target_column_names)
        != len(parsed_projections)
    ):
        raise UnsupportedColumnLineageError(
            "INSERT 目标字段数量和 SELECT 字段数量不一致："
            f"目标字段 {len(target_column_names)} 个，"
            f"SELECT 字段 {len(parsed_projections)} 个"
        )

    # --------------------------------------------------------
    # 按位置建立来源字段和目标字段的映射
    # --------------------------------------------------------

    mappings: list[
        DirectColumnMapping
    ] = []

    for (
        ordinal_position,
        (
            target_column_name,
            parsed_projection,
        ),
    ) in enumerate(
        zip(
            target_column_names,
            parsed_projections,
            strict=True,
        ),
        start=1,
    ):
        (
            source_column_name,
            expression_text,
        ) = parsed_projection

        mappings.append(
            DirectColumnMapping(
                ordinal_position=(
                    ordinal_position
                ),
                source_table_full_name=(
                    source_table_full_name
                ),
                source_column_name=(
                    source_column_name
                ),
                target_table_full_name=(
                    target_table_full_name
                ),
                target_column_name=(
                    target_column_name
                ),
                expression_text=(
                    expression_text
                ),
            )
        )

    return DirectColumnLineageResult(
        dialect=normalized_dialect,
        source_table_full_name=(
            source_table_full_name
        ),
        target_table_full_name=(
            target_table_full_name
        ),
        mappings=tuple(mappings),
    )
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError


# ============================================================
# 1. 自定义异常
# ============================================================

class UnsupportedColumnLineageError(
    ValueError
):
    """
    SQL语法可能正确，但当前版本暂时无法可靠解析其字段血缘。

    使用单独的异常类型，可以区分：

    1. SQL语法错误；
    2. SQL语法正确，但当前解析能力暂不支持；
    3. 普通参数错误。
    """


# ============================================================
# 2. 单条字段映射
# ============================================================

@dataclass(frozen=True)
class DirectColumnMapping:
    """
    一条来源字段到目标字段的映射。

    类名继续保留 DirectColumnMapping，
    是为了兼容前面已经完成的持久化服务和测试。

    实际上现在已经支持：

        direct
        transform
        aggregate

    示例1：直接映射

        ods.orders.order_id
            -> dwd.orders.order_id

    示例2：转换映射

        ods.orders.price
               \
                -> dwd.orders.amount
               /
        ods.orders.quantity

    示例3：JOIN多来源表映射

        ods.orders.amount
               \
                -> dwd.order_detail.final_amount
               /
        ods.customer.discount_rate
    """

    ordinal_position: int

    source_table_full_name: str
    source_column_name: str

    target_table_full_name: str
    target_column_name: str

    expression_text: str

    relation_type: str = "direct"


# ============================================================
# 3. 整条SQL的字段血缘结果
# ============================================================

@dataclass(frozen=True)
class DirectColumnLineageResult:
    """
    一条 INSERT INTO ... SELECT ... SQL 的字段血缘结果。

    source_table_full_names:
        SQL中识别到的所有物理来源表。

    target_table_full_name:
        INSERT写入的目标表。

    mappings:
        所有来源字段到目标字段的映射。
    """

    dialect: str

    source_table_full_names: tuple[
        str,
        ...
    ]

    target_table_full_name: str

    mappings: tuple[
        DirectColumnMapping,
        ...
    ]

    @property
    def source_table_full_name(
        self,
    ) -> str:
        """
        保留单数形式属性，兼容以前的单来源表代码。

        单来源表SQL可以继续使用：

            result.source_table_full_name

        多来源表SQL必须使用：

            result.source_table_full_names
        """

        if (
            len(
                self.source_table_full_names
            )
            != 1
        ):
            raise UnsupportedColumnLineageError(
                "当前SQL包含多个来源表，"
                "请使用 source_table_full_names"
            )

        return (
            self.source_table_full_names[0]
        )


# ============================================================
# 4. 生成完整表名
# ============================================================

def _table_full_name(
    table_expression: exp.Table,
) -> str:
    """
    将SQLGlot的Table节点转换成完整表名。

    支持：

        orders
        ods.orders
        catalog.ods.orders
    """

    table_name = (
        table_expression.name
    )

    if not table_name:
        raise UnsupportedColumnLineageError(
            "SQL中的数据表名称为空"
        )

    parts: list[str] = []

    catalog_name = (
        table_expression.catalog
    )

    schema_name = (
        table_expression.db
    )

    if catalog_name:
        parts.append(catalog_name)

    if schema_name:
        parts.append(schema_name)

    parts.append(table_name)

    return ".".join(parts)


# ============================================================
# 5. 提取INSERT目标表和目标字段
# ============================================================

def _extract_insert_target(
    insert_expression: exp.Insert,
) -> tuple[
    exp.Table,
    tuple[str, ...],
]:
    """
    从INSERT节点提取：

        目标表
        目标字段列表

    当前要求INSERT显式声明目标字段。

    支持：

        INSERT INTO dwd.orders (
            order_id,
            amount
        )
        SELECT ...

    暂不支持：

        INSERT INTO dwd.orders
        SELECT ...

    因为没有目标字段列表时，
    必须读取目标表的真实数据库元数据，
    才能可靠判断SELECT字段的位置对应关系。
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
        column_name = (
            column_expression.name
        )

        if not column_name:
            raise UnsupportedColumnLineageError(
                "INSERT目标字段名称为空"
            )

        target_column_names.append(
            column_name
        )

    if not target_column_names:
        raise UnsupportedColumnLineageError(
            "INSERT没有声明目标字段"
        )

    return (
        target_table_expression,
        tuple(target_column_names),
    )


# ============================================================
# 6. 提取INSERT中的SELECT
# ============================================================

def _extract_select(
    insert_expression: exp.Insert,
) -> exp.Select:
    """
    获取INSERT后面的SELECT。

    当前只支持：

        INSERT INTO ... SELECT ...

    暂不支持：

        INSERT ... UNION ...
        INSERT ... VALUES ...
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
    当前版本已经支持普通JOIN。

    仍然暂不支持：

        CTE
        子查询

    CTE和子查询会产生独立字段作用域，
    不能简单地把内部物理表字段直接绑定到最终目标字段。
    """

    cte_expression = next(
        select_expression.find_all(
            exp.CTE
        ),
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
# 8. 提取来源表和表别名
# ============================================================

def _extract_source_tables(
    select_expression: exp.Select,
) -> tuple[
    tuple[str, ...],
    dict[str, str],
]:
    """
    提取SELECT中的所有物理来源表，
    同时建立限定符到真实表名的映射。

    示例：

        FROM ods.orders AS o
        JOIN ods.customer AS c

    返回来源表：

        (
            "ods.orders",
            "ods.customer",
        )

    返回限定符映射：

        {
            "o": "ods.orders",
            "c": "ods.customer",
            "orders": "ods.orders",
            "customer": "ods.customer"
        }

    当不同Schema下出现同名表时，例如：

        ods.orders
        dwd.orders

    简单限定符orders会产生歧义，
    因此会从映射中移除。
    """

    table_expressions = list(
        select_expression.find_all(
            exp.Table
        )
    )

    if not table_expressions:
        raise UnsupportedColumnLineageError(
            "SELECT中没有找到来源数据表"
        )

    source_table_full_names: list[
        str
    ] = []

    source_table_by_qualifier: dict[
        str,
        str,
    ] = {}

    ambiguous_qualifiers: set[
        str
    ] = set()

    for table_expression in (
        table_expressions
    ):
        full_name = _table_full_name(
            table_expression
        )

        if (
            full_name
            not in source_table_full_names
        ):
            source_table_full_names.append(
                full_name
            )

        qualifiers: set[str] = {
            full_name,
            full_name.lower(),
            table_expression.name,
            table_expression.name.lower(),
        }

        alias_name = (
            table_expression.alias
        )

        if alias_name:
            qualifiers.add(alias_name)
            qualifiers.add(
                alias_name.lower()
            )

        for qualifier in qualifiers:
            if not qualifier:
                continue

            if (
                qualifier
                in ambiguous_qualifiers
            ):
                continue

            existing_full_name = (
                source_table_by_qualifier
                .get(qualifier)
            )

            if (
                existing_full_name
                is not None
                and existing_full_name
                != full_name
            ):
                source_table_by_qualifier.pop(
                    qualifier,
                    None,
                )

                ambiguous_qualifiers.add(
                    qualifier
                )

                continue

            source_table_by_qualifier[
                qualifier
            ] = full_name

    return (
        tuple(source_table_full_names),
        source_table_by_qualifier,
    )


# ============================================================
# 9. 提取投影表达式中的来源字段
# ============================================================

def _extract_projection_sources(
    projection_expression: exp.Expression,
    dialect: str | None,
    source_table_full_names: tuple[
        str,
        ...
    ],
    source_table_by_qualifier: dict[
        str,
        str,
    ],
) -> tuple[
    tuple[
        tuple[str, str],
        ...
    ],
    str,
    str,
]:
    """
    提取一个SELECT投影表达式中的所有来源字段。

    返回：

        (
            来源字段集合,
            表达式文本,
            血缘关系类型,
        )

    来源字段格式：

        (
            来源表完整名称,
            来源字段名称,
        )

    示例1：

        o.order_id

    返回：

        (
            (
                "ods.orders",
                "order_id",
            ),
        )

    示例2：

        o.amount * c.discount_rate
            AS final_amount

    返回：

        (
            (
                "ods.orders",
                "amount",
            ),
            (
                "ods.customer",
                "discount_rate",
            ),
        )
    """

    if dialect is None:
        expression_text = (
            projection_expression.sql()
        )
    else:
        expression_text = (
            projection_expression.sql(
                dialect=dialect,
            )
        )

    # --------------------------------------------------------
    # 去掉最外层AS别名
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 拒绝星号
    # --------------------------------------------------------

    if isinstance(
        source_expression,
        exp.Star,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段暂不支持 SELECT *"
        )

    contains_star = any(
        isinstance(node, exp.Star)
        for node in source_expression.walk()
    )

    if contains_star:
        raise UnsupportedColumnLineageError(
            "当前阶段暂不支持包含 * 的字段表达式："
            f"{expression_text}"
        )

    # --------------------------------------------------------
    # 查找表达式中的所有Column节点
    # --------------------------------------------------------

    column_expressions = list(
        source_expression.find_all(
            exp.Column
        )
    )

    # 兼容不同SQLGlot版本：
    # 根节点本身是Column时，
    # find_all有可能不返回根节点。
    if (
        isinstance(
            source_expression,
            exp.Column,
        )
        and not column_expressions
    ):
        column_expressions = [
            source_expression
        ]

    if not column_expressions:
        raise UnsupportedColumnLineageError(
            "字段表达式中没有找到来源字段："
            f"{expression_text}"
        )

    # --------------------------------------------------------
    # 解析字段所属的真实来源表
    # --------------------------------------------------------

    source_fields: list[
        tuple[str, str]
    ] = []

    seen_source_fields: set[
        tuple[str, str]
    ] = set()

    for column_expression in (
        column_expressions
    ):
        column_name = (
            column_expression.name
        )

        if not column_name:
            continue

        qualifier = (
            column_expression.table
        )

        # ----------------------------------------------------
        # 有表限定符
        #
        # 例如：
        #
        #     o.order_id
        #     orders.order_id
        # ----------------------------------------------------

        if qualifier:
            source_table_full_name = (
                source_table_by_qualifier
                .get(qualifier)
            )

            if source_table_full_name is None:
                source_table_full_name = (
                    source_table_by_qualifier
                    .get(
                        qualifier.lower()
                    )
                )

            if source_table_full_name is None:
                raise UnsupportedColumnLineageError(
                    "无法解析字段的表别名："
                    f"字段={column_expression.sql()}, "
                    f"别名={qualifier}"
                )

        # ----------------------------------------------------
        # 没有表限定符
        #
        # 单来源表时，可以确定字段归属。
        #
        # 多来源表时，不能确定属于哪张表，
        # 因此不能自行猜测。
        # ----------------------------------------------------

        else:
            if (
                len(
                    source_table_full_names
                )
                != 1
            ):
                raise UnsupportedColumnLineageError(
                    "JOIN查询中的字段必须使用"
                    "表名或别名限定："
                    f"{column_name}"
                )

            source_table_full_name = (
                source_table_full_names[0]
            )

        source_field = (
            source_table_full_name,
            column_name,
        )

        # 同一表达式中同一个字段重复出现时，
        # 只生成一条来源关系。
        #
        # 例如：
        #
        #     amount + amount
        #
        # 只生成一次amount来源。
        if (
            source_field
            in seen_source_fields
        ):
            continue

        seen_source_fields.add(
            source_field
        )

        source_fields.append(
            source_field
        )

    if not source_fields:
        raise UnsupportedColumnLineageError(
            "字段表达式中没有找到有效来源字段："
            f"{expression_text}"
        )

    # --------------------------------------------------------
    # 判断关系类型
    # --------------------------------------------------------
    #
    # 单独一个Column：
    #     direct
    #
    # 包含SUM、COUNT等聚合函数：
    #     aggregate
    #
    # 其他计算、CASE、普通函数：
    #     transform
    # --------------------------------------------------------

    if isinstance(
        source_expression,
        exp.Column,
    ):
        relation_type = "direct"

    else:
        contains_aggregate = any(
            isinstance(
                node,
                exp.AggFunc,
            )
            for node in (
                source_expression.walk()
            )
        )

        if contains_aggregate:
            relation_type = "aggregate"
        else:
            relation_type = "transform"

    return (
        tuple(source_fields),
        expression_text,
        relation_type,
    )

# ============================================================
# 10. 查找WITH节点
# ============================================================

def _find_with_expression(
    insert_expression: exp.Insert,
    select_expression: exp.Select,
) -> exp.With | None:
    """
    SQLGlot不同版本可能把WITH节点保存在：

        Insert.args["with_"]

    或：

        Select.args["with_"]

    因此两个位置都检查。
    """

    for expression in (
        insert_expression,
        select_expression,
    ):
        with_expression = (
            expression.args.get("with_")
            or expression.args.get("with")
        )

        if isinstance(
            with_expression,
            exp.With,
        ):
            return with_expression

    # 最后通过表达式树搜索一次，
    # 兼容其他SQLGlot版本。
    return next(
        insert_expression.find_all(
            exp.With
        ),
        None,
    )


# ============================================================
# 11. 取得CTE中的SELECT
# ============================================================

def _get_cte_select(
    cte_expression: exp.CTE,
) -> exp.Select:
    """
    取得CTE内部的SELECT。

    SQLGlot通常把CTE查询直接放在：

        cte_expression.this

    少数结构可能外层是Subquery，
    因此同时进行兼容处理。
    """

    query_expression = (
        cte_expression.this
    )

    if isinstance(
        query_expression,
        exp.Subquery,
    ):
        query_expression = (
            query_expression.this
        )

    if not isinstance(
        query_expression,
        exp.Select,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段只支持CTE内部为普通SELECT"
        )

    return query_expression


# ============================================================
# 12. 取得SELECT直接FROM的表
# ============================================================

def _get_direct_from_table(
    select_expression: exp.Select,
) -> exp.Table:
    """
    只读取当前SELECT自己的FROM表，
    不向下递归读取CTE内部的物理表。
    """

    from_expression = (
        select_expression.args.get(
            "from_"
        )
        or select_expression.args.get(
            "from"
        )
    )

    if from_expression is None:
        raise UnsupportedColumnLineageError(
            "SELECT中没有找到FROM"
        )

    table_expression = (
        from_expression.this
    )

    if not isinstance(
        table_expression,
        exp.Table,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段要求CTE最终查询"
            "直接FROM一个CTE"
        )

    joins = (
        select_expression.args.get(
            "joins"
        )
        or []
    )

    if joins:
        raise UnsupportedColumnLineageError(
            "当前阶段暂不支持CTE最终查询继续JOIN"
        )

    return table_expression


# ============================================================
# 13. 获取CTE投影输出名称
# ============================================================

def _get_projection_output_name(
    projection_expression: exp.Expression,
) -> str:
    """
    获取CTE中一个投影字段的输出名称。

    支持：

        order_id

        price * quantity AS amount

    计算表达式必须显式使用AS别名。
    """

    if isinstance(
        projection_expression,
        exp.Alias,
    ):
        output_name = (
            projection_expression.alias
        )

        if not output_name:
            raise UnsupportedColumnLineageError(
                "CTE字段别名为空"
            )

        return output_name

    if isinstance(
        projection_expression,
        exp.Column,
    ):
        output_name = (
            projection_expression.name
        )

        if not output_name:
            raise UnsupportedColumnLineageError(
                "CTE字段名称为空"
            )

        return output_name

    expression_text = (
        projection_expression.sql()
    )

    raise UnsupportedColumnLineageError(
        "CTE中的计算表达式必须使用AS别名："
        f"{expression_text}"
    )


# ============================================================
# 14. 解析单层单个CTE
# ============================================================
def _extract_direct_select_tables(
    select_expression: exp.Select,
) -> tuple[exp.Table, ...]:
    """
    只提取当前 SELECT 的直接数据源。

    例如：

        SELECT o.order_id
        FROM order_base AS o
        JOIN customer_base AS c
          ON o.customer_id = c.customer_id

    返回：

        order_base AS o
        customer_base AS c

    不会递归进入 CTE 内部查找物理表。
    """

    result: list[exp.Table] = []

    # 不同 SQLGlot 版本可能使用 from 或 from_。
    from_expression = (
        select_expression.args.get("from_")
        or select_expression.args.get("from")
    )

    if from_expression is not None:
        source_expression = from_expression.this

        if isinstance(source_expression, exp.Table):
            result.append(source_expression)

        elif source_expression is not None:
            raise UnsupportedColumnLineageError(
                "当前阶段不支持最终 SELECT 的 FROM 子查询"
            )

    for join_expression in (
        select_expression.args.get("joins")
        or ()
    ):
        source_expression = join_expression.this

        if isinstance(source_expression, exp.Table):
            result.append(source_expression)

        elif source_expression is not None:
            raise UnsupportedColumnLineageError(
                "当前阶段不支持最终 SELECT 的 JOIN 子查询"
            )

    if not result:
        raise UnsupportedColumnLineageError(
            "最终 SELECT 没有找到直接数据源"
        )

    return tuple(result)

def _extract_cte_lineage(
    insert_expression: exp.Insert,
    select_expression: exp.Select,
    with_expression: exp.With,
    target_table_full_name: str,
    target_column_names: tuple[str, ...],
    normalized_dialect: str,
    render_dialect: str | None,
) -> DirectColumnLineageResult:
    """
    解析一个或多个并列 CTE 的字段血缘。

    当前阶段支持：

        WITH cte_a AS (...),
             cte_b AS (...)
        INSERT INTO target_table (...)
        SELECT ...
        FROM cte_a
        JOIN cte_b ...

    当前阶段暂时不支持：

        1. CTE 继续读取另一个 CTE；
        2. 最终 SELECT 中再次计算表达式；
        3. CTE 内部嵌套子查询；
        4. UNION / INTERSECT / EXCEPT；
        5. SELECT *。
    """

    cte_expressions = tuple(
        with_expression.expressions
    )

    if not cte_expressions:
        raise UnsupportedColumnLineageError(
            "WITH 中没有找到 CTE"
        )

    # --------------------------------------------------------
    # 第一步：取得全部 CTE 名称
    # --------------------------------------------------------

    cte_name_by_lower: dict[str, str] = {}

    for cte_expression in cte_expressions:
        cte_name = cte_expression.alias_or_name

        if not cte_name:
            raise UnsupportedColumnLineageError(
                "CTE 必须有名称"
            )

        normalized_cte_name = cte_name.lower()

        if normalized_cte_name in cte_name_by_lower:
            raise UnsupportedColumnLineageError(
                f"存在重复的 CTE 名称：{cte_name}"
            )

        cte_name_by_lower[normalized_cte_name] = cte_name

    # --------------------------------------------------------
    # 第二步：解析每一个 CTE 的输出字段
    #
    # 结构：
    #
    # {
    #     "order_base": {
    #         "order_id": {
    #             "source_fields": (
    #                 ("ods.orders", "order_id"),
    #             ),
    #             "expression_text": "order_id",
    #             "relation_type": "direct",
    #         },
    #         "amount": {
    #             "source_fields": (
    #                 ("ods.orders", "price"),
    #                 ("ods.orders", "quantity"),
    #             ),
    #             "expression_text": "price * quantity",
    #             "relation_type": "transform",
    #         },
    #     },
    # }
    # --------------------------------------------------------

    cte_output_lineage: dict[
        str,
        dict[str, dict[str, object]],
    ] = {}

    for cte_expression in cte_expressions:
        cte_name = cte_expression.alias_or_name
        normalized_cte_name = cte_name.lower()

        cte_select = _get_cte_select(
            cte_expression
        )

        _validate_simple_select(
            cte_select
        )

        (
            source_table_full_names,
            source_table_by_qualifier,
        ) = _extract_source_tables(
            cte_select
        )

        # 本阶段只支持并列 CTE。
        #
        # 例如下面这种依赖关系暂时不支持：
        #
        # WITH a AS (...),
        #      b AS (SELECT * FROM a)
        for source_table_full_name in (
            source_table_full_names
        ):
            source_simple_name = (
                source_table_full_name
                .split(".")[-1]
                .lower()
            )

            if source_simple_name in cte_name_by_lower:
                raise UnsupportedColumnLineageError(
                    "当前阶段暂不支持 CTE 依赖另一个 CTE："
                    f"{cte_name} 读取了 "
                    f"{cte_name_by_lower[source_simple_name]}"
                )

        output_by_name: dict[
            str,
            dict[str, object],
        ] = {}

        for projection_expression in (
            cte_select.expressions
        ):
            output_name = (
                _get_projection_output_name(
                    projection_expression
                )
            )

            normalized_output_name = (
                output_name.lower()
            )

            if normalized_output_name in output_by_name:
                raise UnsupportedColumnLineageError(
                    f"CTE {cte_name} 存在重复输出字段："
                    f"{output_name}"
                )

            (
                source_fields,
                expression_text,
                relation_type,
            ) = _extract_projection_sources(
                projection_expression=projection_expression,
                dialect=render_dialect,
                source_table_full_names=(
                    source_table_full_names
                ),
                source_table_by_qualifier=(
                    source_table_by_qualifier
                ),
            )
            if isinstance(
                    projection_expression,
                    exp.Alias,
            ):
                lineage_expression = (
                    projection_expression.this
                )
            else:
                lineage_expression = (
                    projection_expression
                )

            expression_text = lineage_expression.sql(
                dialect=render_dialect
            )
            if not source_fields:
                raise UnsupportedColumnLineageError(
                    f"CTE {cte_name} 的字段 "
                    f"{output_name} 没有找到物理来源字段"
                )

            output_by_name[
                normalized_output_name
            ] = {
                "source_fields": source_fields,
                "expression_text": expression_text,
                "relation_type": relation_type,
            }

        cte_output_lineage[
            normalized_cte_name
        ] = output_by_name

    # --------------------------------------------------------
    # 第三步：分析最终 SELECT 使用了哪些 CTE
    # --------------------------------------------------------

    direct_source_tables = (
        _extract_direct_select_tables(
            select_expression
        )
    )

    cte_name_by_qualifier: dict[str, str] = {}
    final_cte_names: list[str] = []

    for table_expression in direct_source_tables:
        table_name = _table_full_name(
            table_expression
        )

        normalized_table_name = (
            table_name.split(".")[-1].lower()
        )

        if normalized_table_name not in cte_name_by_lower:
            raise UnsupportedColumnLineageError(
                "使用 WITH 时，最终 SELECT 只能读取已定义的 "
                f"CTE；当前读取了：{table_name}"
            )

        cte_name = cte_name_by_lower[
            normalized_table_name
        ]

        if cte_name not in final_cte_names:
            final_cte_names.append(cte_name)

        # CTE 原始名称可以作为限定符。
        cte_name_by_qualifier[
            cte_name.lower()
        ] = cte_name

        # 表达式中的实际名称也可以作为限定符。
        cte_name_by_qualifier[
            table_expression.name.lower()
        ] = cte_name

        # 别名也可以作为限定符。
        #
        # 例如：
        #
        # FROM order_base AS o
        table_alias = table_expression.alias

        if table_alias:
            cte_name_by_qualifier[
                table_alias.lower()
            ] = cte_name

    # --------------------------------------------------------
    # 第四步：验证 INSERT 字段数量
    # --------------------------------------------------------

    final_projections = tuple(
        select_expression.expressions
    )

    if len(target_column_names) != len(
        final_projections
    ):
        raise UnsupportedColumnLineageError(
            "INSERT 目标字段数量和 SELECT 字段数量不一致："
            f"目标字段 {len(target_column_names)} 个，"
            f"SELECT 字段 {len(final_projections)} 个"
        )

    # --------------------------------------------------------
    # 第五步：把最终 SELECT 字段展开到物理来源字段
    # --------------------------------------------------------

    mappings: list[DirectColumnMapping] = []

    for ordinal_position, (
        target_column_name,
        projection_expression,
    ) in enumerate(
        zip(
            target_column_names,
            final_projections,
        ),
        start=1,
    ):
        # 支持：
        #
        # SELECT o.order_id
        #
        # 也支持：
        #
        # SELECT o.order_id AS order_id
        if isinstance(
            projection_expression,
            exp.Alias,
        ):
            final_expression = (
                projection_expression.this
            )
        else:
            final_expression = (
                projection_expression
            )

        # 这一阶段最终 SELECT 只能引用 CTE 输出字段，
        # 不能再次计算。
        #
        # CTE 内部仍然可以有：
        #
        # price * quantity AS amount
        #
        # 但是最终 SELECT 必须写：
        #
        # SELECT o.amount
        if not isinstance(
            final_expression,
            exp.Column,
        ):
            raise UnsupportedColumnLineageError(
                "多个 CTE 场景下，最终 SELECT 暂时只支持"
                "直接引用 CTE 输出字段，不支持再次计算："
                f"{projection_expression.sql(dialect=render_dialect)}"
            )

        output_column_name = (
            final_expression.name
        )

        qualifier = final_expression.table

        # ----------------------------------------------------
        # 有限定符：
        #
        # o.order_id
        # c.customer_name
        # ----------------------------------------------------

        if qualifier:
            cte_name = cte_name_by_qualifier.get(
                qualifier.lower()
            )

            if cte_name is None:
                raise UnsupportedColumnLineageError(
                    f"无法识别字段限定符：{qualifier}"
                )

        # ----------------------------------------------------
        # 没有限定符：
        #
        # SELECT order_id
        #
        # 如果只有一个 CTE，直接使用它。
        #
        # 如果有多个 CTE，就检查该字段只在哪一个 CTE
        # 中出现。出现多次则认为含义不明确。
        # ----------------------------------------------------

        elif len(final_cte_names) == 1:
            cte_name = final_cte_names[0]

        else:
            matched_cte_names = [
                candidate_cte_name
                for candidate_cte_name
                in final_cte_names
                if output_column_name.lower()
                in cte_output_lineage[
                    candidate_cte_name.lower()
                ]
            ]

            if len(matched_cte_names) == 0:
                raise UnsupportedColumnLineageError(
                    "没有任何 CTE 输出字段："
                    f"{output_column_name}"
                )

            if len(matched_cte_names) > 1:
                raise UnsupportedColumnLineageError(
                    "字段没有表别名，无法唯一确定它来自哪个 "
                    f"CTE：{output_column_name}"
                )

            cte_name = matched_cte_names[0]

        # ----------------------------------------------------
        # 查询 CTE 输出字段对应的物理来源
        # ----------------------------------------------------

        cte_outputs = cte_output_lineage[
            cte_name.lower()
        ]

        output_lineage = cte_outputs.get(
            output_column_name.lower()
        )

        if output_lineage is None:
            raise UnsupportedColumnLineageError(
                f"CTE {cte_name} 没有输出字段："
                f"{output_column_name}"
            )

        source_fields = output_lineage[
            "source_fields"
        ]

        expression_text = output_lineage[
            "expression_text"
        ]

        relation_type = output_lineage[
            "relation_type"
        ]

        # 一个目标字段可能对应多个来源字段。
        #
        # 例如：
        #
        # price * quantity AS amount
        #
        # 会产生：
        #
        # price    -> amount
        # quantity -> amount
        for (
            source_table_full_name,
            source_column_name,
        ) in source_fields:
            mappings.append(
                DirectColumnMapping(
                    ordinal_position=ordinal_position,
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
                    relation_type=(
                        relation_type
                    ),
                )
            )

    # --------------------------------------------------------
    # 第六步：汇总真正使用到的物理来源表
    # --------------------------------------------------------

    used_source_table_full_names = tuple(
        dict.fromkeys(
            mapping.source_table_full_name
            for mapping in mappings
        )
    )

    return DirectColumnLineageResult(
        dialect=normalized_dialect,
        source_table_full_names=(
            used_source_table_full_names
        ),
        target_table_full_name=(
            target_table_full_name
        ),
        mappings=tuple(mappings),
    )
# ============================================================
# 10. 正式公开函数
# ============================================================

def extract_direct_column_lineage(
    sql_text: str,
    dialect: str,
) -> DirectColumnLineageResult:
    """
    从一条INSERT INTO ... SELECT ... SQL中提取字段血缘。

    支持：

        单来源表
        多来源JOIN
        表别名
        直接字段
        字段别名
        数学计算
        CASE WHEN
        普通函数
        聚合函数

    目标字段按照位置与SELECT投影对应。

    例如：

        INSERT INTO target_table (
            target_a,
            target_b
        )
        SELECT
            source_x,
            source_y
        FROM source_table;

    对应：

        source_x -> target_a
        source_y -> target_b
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

    # unknown不能直接作为SQLGlot方言名称。
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
            f"SQL解析失败：{exc}"
        ) from exc

    if not isinstance(
        expression,
        exp.Insert,
    ):
        raise UnsupportedColumnLineageError(
            "当前阶段只支持 INSERT INTO ... SELECT ..."
        )

    # --------------------------------------------------------
    # 提取目标表和目标字段
    # --------------------------------------------------------

    (
        target_table_expression,
        target_column_names,
    ) = _extract_insert_target(
        expression
    )

    target_table_full_name = (
        _table_full_name(
            target_table_expression
        )
    )

    # --------------------------------------------------------
    # 提取SELECT
    # --------------------------------------------------------

    select_expression = _extract_select(
        expression
    )

    # --------------------------------------------------------
    # 优先检查CTE
    # --------------------------------------------------------

    with_expression = (
        _find_with_expression(
            insert_expression=expression,
            select_expression=(
                select_expression
            ),
        )
    )

    if with_expression is not None:
        return _extract_cte_lineage(
            insert_expression=expression,
            select_expression=select_expression,
            with_expression=with_expression,
            target_table_full_name=target_table_full_name,
            target_column_names=target_column_names,
            normalized_dialect=normalized_dialect,
            render_dialect=read_dialect,
        )

    # --------------------------------------------------------
    # 非CTE走原来的普通SELECT流程
    # --------------------------------------------------------

    _validate_simple_select(
        select_expression
    )

    (
        source_table_full_names,
        source_table_by_qualifier,
    ) = _extract_source_tables(
        select_expression
    )

    # --------------------------------------------------------
    # 读取SELECT投影
    # --------------------------------------------------------

    projection_expressions = tuple(
        select_expression.expressions
    )

    # --------------------------------------------------------
    # 先解析投影
    #
    # 必须在字段数量检查之前解析，
    # 这样SELECT *可以得到明确错误，
    # 而不是只报告字段数量不一致。
    # --------------------------------------------------------

    parsed_projections: list[
        tuple[
            tuple[
                tuple[str, str],
                ...
            ],
            str,
            str,
        ]
    ] = []

    for projection_expression in (
        projection_expressions
    ):
        (
            source_fields,
            expression_text,
            relation_type,
        ) = _extract_projection_sources(
            projection_expression=(
                projection_expression
            ),
            dialect=read_dialect,
            source_table_full_names=(
                source_table_full_names
            ),
            source_table_by_qualifier=(
                source_table_by_qualifier
            ),
        )

        parsed_projections.append(
            (
                source_fields,
                expression_text,
                relation_type,
            )
        )

    # --------------------------------------------------------
    # 检查目标字段数量和SELECT投影数量
    # --------------------------------------------------------
    #
    # 注意比较的是投影数量，
    # 不是来源字段数量。
    #
    # price * quantity是一个投影，
    # 但会生成两个来源字段映射。
    # --------------------------------------------------------

    if (
        len(target_column_names)
        != len(parsed_projections)
    ):
        raise UnsupportedColumnLineageError(
            "INSERT目标字段数量和SELECT字段数量不一致："
            f"目标字段 {len(target_column_names)} 个，"
            f"SELECT字段 {len(parsed_projections)} 个"
        )

    mappings: list[
        DirectColumnMapping
    ] = []

    # --------------------------------------------------------
    # 按投影位置绑定目标字段
    # --------------------------------------------------------

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
            source_fields,
            expression_text,
            relation_type,
        ) = parsed_projection

        # 一个投影中可能包含多个来源字段。
        #
        # 每个来源字段分别创建一条Mapping，
        # 但都指向相同的目标字段。
        for (
            source_table_full_name,
            source_column_name,
        ) in source_fields:
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
                    relation_type=(
                        relation_type
                    ),
                )
            )

    return DirectColumnLineageResult(
        dialect=normalized_dialect,
        source_table_full_names=(
            source_table_full_names
        ),
        target_table_full_name=(
            target_table_full_name
        ),
        mappings=tuple(mappings),
    )
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
        # expression_text 只保存真正的字段计算表达式，
        # 不包含 SELECT 输出别名。
        #
        # 例如：
        #
        #     price * quantity AS amount
        #
        # 保存为：
        #
        #     price * quantity
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
            dialect=dialect
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
def _merge_cte_relation_type(
    current_relation_type: str,
    upstream_relation_types: tuple[str, ...],
) -> str:
    """
    合并当前 CTE 字段和上游 CTE 字段的关系类型。

    例如：

        第一层：
            price * quantity AS amount
            relation_type = transform

        第二层：
            SELECT amount
            relation_type = direct

    虽然第二层只是直接引用 amount，
    但追溯到物理来源后，最终关系仍然应该是：

        transform

    合并规则：

        当前层是 aggregate
            -> aggregate

        当前层是 transform
            -> transform

        当前层是 direct
            -> 继承上游关系

        上游包含 aggregate
            -> aggregate

        上游包含 transform
            -> transform

        否则
            -> direct
    """

    if current_relation_type == "aggregate":
        return "aggregate"

    if current_relation_type == "transform":
        return "transform"

    if "aggregate" in upstream_relation_types:
        return "aggregate"

    if "transform" in upstream_relation_types:
        return "transform"

    return current_relation_type


def _expand_cte_source_fields(
    *,
    current_cte_name: str,
    source_fields: tuple[
        tuple[str, str],
        ...,
    ],
    expression_text: str,
    relation_type: str,
    cte_name_by_lower: dict[str, str],
    cte_output_lineage: dict[
        str,
        dict[str, dict[str, object]],
    ],
) -> tuple[
    tuple[tuple[str, str], ...],
    str,
    str,
]:
    """
    把当前 CTE 引用的上游 CTE 字段，
    展开成真正的物理表字段。

    例如当前字段来源是：

        ("order_base", "amount")

    而 order_base.amount 的来源是：

        ("ods.orders", "price")
        ("ods.orders", "quantity")

    展开后返回：

        (
            ("ods.orders", "price"),
            ("ods.orders", "quantity"),
        )

    同时继承：

        expression_text = "price * quantity"
        relation_type = "transform"
    """

    expanded_source_fields: list[
        tuple[str, str]
    ] = []

    upstream_relation_types: list[str] = []

    inherited_expression_text: str | None = None

    cte_reference_count = 0

    for (
        source_table_full_name,
        source_column_name,
    ) in source_fields:
        source_simple_name = (
            source_table_full_name
            .split(".")[-1]
            .lower()
        )

        # ----------------------------------------------------
        # 不是 CTE，说明已经是物理表字段
        # ----------------------------------------------------

        if source_simple_name not in cte_name_by_lower:
            expanded_source_fields.append(
                (
                    source_table_full_name,
                    source_column_name,
                )
            )

            continue

        # ----------------------------------------------------
        # 是 CTE，需要继续向上游展开
        # ----------------------------------------------------

        upstream_cte_name = cte_name_by_lower[
            source_simple_name
        ]

        normalized_upstream_cte_name = (
            upstream_cte_name.lower()
        )

        # CTE 必须先定义再使用。
        #
        # 支持：
        #
        # WITH a AS (...),
        #      b AS (SELECT ... FROM a)
        #
        # 暂不支持：
        #
        # WITH b AS (SELECT ... FROM a),
        #      a AS (...)
        #
        # 也可以防止 CTE 自己引用自己。
        if (
            normalized_upstream_cte_name
            not in cte_output_lineage
        ):
            raise UnsupportedColumnLineageError(
                "检测到 CTE 前向引用、循环引用，"
                "或者 CTE 自己引用自己："
                f"{current_cte_name} -> "
                f"{upstream_cte_name}"
            )

        upstream_outputs = cte_output_lineage[
            normalized_upstream_cte_name
        ]

        upstream_output = upstream_outputs.get(
            source_column_name.lower()
        )

        if upstream_output is None:
            raise UnsupportedColumnLineageError(
                f"CTE {upstream_cte_name} "
                f"没有输出字段：{source_column_name}"
            )

        upstream_source_fields = upstream_output[
            "source_fields"
        ]

        upstream_expression_text = upstream_output[
            "expression_text"
        ]

        upstream_relation_type = upstream_output[
            "relation_type"
        ]

        # 上游 CTE 在保存进 cte_output_lineage 前，
        # 已经完成过一次展开。
        #
        # 因此这里取到的 source_fields 应该已经是：
        #
        #     物理表字段
        #
        # 而不是另一个中间 CTE。
        expanded_source_fields.extend(
            upstream_source_fields
        )

        upstream_relation_types.append(
            str(upstream_relation_type)
        )

        cte_reference_count += 1

        # 当前字段只是直接引用一个上游 CTE 字段时，
        # 应继承上游真正的计算表达式。
        #
        # 例如：
        #
        # order_base:
        #     price * quantity AS amount
        #
        # order_enriched:
        #     SELECT amount
        #
        # order_enriched.amount 的表达式仍然应该保存为：
        #
        #     price * quantity
        if (
            relation_type == "direct"
            and len(source_fields) == 1
        ):
            inherited_expression_text = str(
                upstream_expression_text
            )

    # --------------------------------------------------------
    # 对物理来源字段去重，同时保持原顺序
    # --------------------------------------------------------

    deduplicated_source_fields = tuple(
        dict.fromkeys(
            expanded_source_fields
        )
    )

    merged_relation_type = (
        _merge_cte_relation_type(
            current_relation_type=relation_type,
            upstream_relation_types=tuple(
                upstream_relation_types
            ),
        )
    )

    if (
        cte_reference_count == 1
        and relation_type == "direct"
        and inherited_expression_text is not None
    ):
        final_expression_text = (
            inherited_expression_text
        )
    else:
        final_expression_text = expression_text

    return (
        deduplicated_source_fields,
        final_expression_text,
        merged_relation_type,
    )

def _unwrap_insert_source_expression(
    source_expression: exp.Expression,
) -> exp.Expression:
    """
    去掉 INSERT 数据源外层可能存在的括号或 Subquery。

    例如：

        INSERT INTO target_table
        (
            SELECT ...
            UNION ALL
            SELECT ...
        )

    需要取得括号里面真正的 Union 或 Select。
    """

    current_expression = source_expression

    while isinstance(
        current_expression,
        (
            exp.Subquery,
            exp.Paren,
        ),
    ):
        current_expression = (
            current_expression.this
        )

    return current_expression

def _flatten_union_all_selects(
    expression: exp.Expression,
) -> tuple[exp.Select, ...]:
    """
    把嵌套的 UNION ALL 展开成普通 SELECT 列表。

    例如：

        SELECT ... FROM a

        UNION ALL

        SELECT ... FROM b

        UNION ALL

        SELECT ... FROM c

    SQLGlot 通常会形成嵌套 Union：

        Union(
            Union(
                Select(a),
                Select(b),
            ),
            Select(c),
        )

    最终展开为：

        (
            Select(a),
            Select(b),
            Select(c),
        )
    """

    expression = (
        _unwrap_insert_source_expression(
            expression
        )
    )

    if isinstance(expression, exp.Select):
        return (
            expression,
        )

    if not isinstance(expression, exp.Union):
        raise UnsupportedColumnLineageError(
            "UNION ALL 分支必须是 SELECT"
        )

    # SQLGlot 中：
    #
    # UNION
    #     distinct=True
    #
    # UNION ALL
    #     distinct=False
    #
    # 当前阶段先只支持 UNION ALL。
    if expression.args.get("distinct") is not False:
        raise UnsupportedColumnLineageError(
            "当前阶段只支持 UNION ALL，"
            "暂不支持会自动去重的 UNION"
        )

    left_expression = expression.this
    right_expression = expression.expression

    if (
        left_expression is None
        or right_expression is None
    ):
        raise UnsupportedColumnLineageError(
            "UNION ALL 缺少左侧或右侧 SELECT"
        )

    left_selects = (
        _flatten_union_all_selects(
            left_expression
        )
    )

    right_selects = (
        _flatten_union_all_selects(
            right_expression
        )
    )

    return (
        *left_selects,
        *right_selects,
    )
def _find_union_with_expression(
    *,
    insert_expression: exp.Insert,
    union_expression: exp.Union,
) -> exp.With | None:
    """
    查找 CTE 的 WITH 节点。

    不同 SQLGlot 版本或不同 SQL 写法中，
    WITH 可能挂在：

        Insert
        Union
        Union 内部的 Select

    所以这里依次检查。
    """

    expressions_to_check: list[
        exp.Expression
    ] = [
        insert_expression,
        union_expression,
    ]

    try:
        expressions_to_check.extend(
            _flatten_union_all_selects(
                union_expression
            )
        )
    except UnsupportedColumnLineageError:
        # UNION 是否合法仍然交给后面的正式解析处理。
        pass

    for current_expression in (
        expressions_to_check
    ):
        with_expression = (
            current_expression.args.get("with_")
            or current_expression.args.get("with")
        )

        if isinstance(
            with_expression,
            exp.With,
        ):
            return with_expression

    return None

def _build_cte_output_lineage_for_union(
    *,
    with_expression: exp.With,
    render_dialect: str | None,
) -> tuple[
    dict[str, str],
    dict[
        str,
        dict[str, dict[str, object]],
    ],
]:
    """
    解析 UNION ALL 前面的全部 CTE。

    返回两个对象。

    第一个：

        {
            "web_base": "web_base",
            "store_base": "store_base",
        }

    第二个：

        {
            "web_base": {
                "order_id": {
                    "source_fields": (
                        ("ods.web_orders", "order_id"),
                    ),
                    "expression_text": "order_id",
                    "relation_type": "direct",
                },
                "amount": {
                    "source_fields": (
                        ("ods.web_orders", "price"),
                        ("ods.web_orders", "quantity"),
                    ),
                    "expression_text": "price * quantity",
                    "relation_type": "transform",
                },
            },
        }

    CTE 输出字段在保存进字典前，
    会先被展开成真正的物理来源字段。
    """

    cte_expressions = tuple(
        with_expression.expressions
    )

    if not cte_expressions:
        raise UnsupportedColumnLineageError(
            "WITH 中没有找到 CTE"
        )

    # ========================================================
    # 1. 收集全部 CTE 名称
    # ========================================================

    cte_name_by_lower: dict[
        str,
        str,
    ] = {}

    for cte_expression in cte_expressions:
        cte_name = (
            cte_expression.alias_or_name
        )

        if not cte_name:
            raise UnsupportedColumnLineageError(
                "CTE 必须有名称"
            )

        normalized_cte_name = (
            cte_name.lower()
        )

        if normalized_cte_name in cte_name_by_lower:
            raise UnsupportedColumnLineageError(
                f"存在重复的 CTE 名称：{cte_name}"
            )

        cte_name_by_lower[
            normalized_cte_name
        ] = cte_name

    # ========================================================
    # 2. 逐个解析 CTE
    # ========================================================

    cte_output_lineage: dict[
        str,
        dict[str, dict[str, object]],
    ] = {}

    for cte_expression in cte_expressions:
        cte_name = (
            cte_expression.alias_or_name
        )

        normalized_cte_name = (
            cte_name.lower()
        )

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

            if (
                normalized_output_name
                in output_by_name
            ):
                raise UnsupportedColumnLineageError(
                    f"CTE {cte_name} "
                    "存在重复输出字段："
                    f"{output_name}"
                )

            (
                source_fields,
                expression_text,
                relation_type,
            ) = _extract_projection_sources(
                projection_expression=(
                    projection_expression
                ),
                dialect=render_dialect,
                source_table_full_names=(
                    source_table_full_names
                ),
                source_table_by_qualifier=(
                    source_table_by_qualifier
                ),
            )

            # 去掉输出字段别名。
            #
            # price * quantity AS amount
            #
            # 保存为：
            #
            # price * quantity
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

            expression_text = (
                lineage_expression.sql(
                    dialect=render_dialect
                )
            )

            if not source_fields:
                raise UnsupportedColumnLineageError(
                    f"CTE {cte_name} 的字段 "
                    f"{output_name} "
                    "没有找到来源字段"
                )

            # 支持 CTE 继续读取前一个 CTE。
            (
                source_fields,
                expression_text,
                relation_type,
            ) = _expand_cte_source_fields(
                current_cte_name=cte_name,
                source_fields=source_fields,
                expression_text=expression_text,
                relation_type=relation_type,
                cte_name_by_lower=(
                    cte_name_by_lower
                ),
                cte_output_lineage=(
                    cte_output_lineage
                ),
            )

            if not source_fields:
                raise UnsupportedColumnLineageError(
                    f"CTE {cte_name} 的字段 "
                    f"{output_name} "
                    "没有找到物理来源字段"
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

    return (
        cte_name_by_lower,
        cte_output_lineage,
    )

def _extract_cte_union_all_lineage(
    *,
    union_expression: exp.Union,
    with_expression: exp.With,
    target_table_full_name: str,
    target_column_names: tuple[str, ...],
    normalized_dialect: str,
    render_dialect: str | None,
) -> DirectColumnLineageResult:
    """
    提取 CTE + UNION ALL 字段血缘。

    处理步骤：

        1. 解析全部 CTE；
        2. 把 CTE 输出字段展开到物理字段；
        3. 拆开 UNION ALL 的 SELECT 分支；
        4. 每个分支按位置对应 INSERT 目标字段；
        5. 将 UNION 分支中的 CTE 字段继续展开；
        6. 合并所有物理来源。
    """

    (
        cte_name_by_lower,
        cte_output_lineage,
    ) = _build_cte_output_lineage_for_union(
        with_expression=with_expression,
        render_dialect=render_dialect,
    )

    select_branches = (
        _flatten_union_all_selects(
            union_expression
        )
    )

    if len(select_branches) < 2:
        raise UnsupportedColumnLineageError(
            "UNION ALL 至少需要两个 SELECT 分支"
        )

    mappings: list[
        DirectColumnMapping
    ] = []

    # ========================================================
    # 逐个解析 UNION ALL 分支
    # ========================================================

    for branch_number, select_expression in enumerate(
        select_branches,
        start=1,
    ):
        _validate_simple_select(
            select_expression
        )

        branch_projections = tuple(
            select_expression.expressions
        )

        if len(branch_projections) != len(
            target_column_names
        ):
            raise UnsupportedColumnLineageError(
                "UNION ALL 第 "
                f"{branch_number} 个 SELECT "
                "字段数量与 INSERT 目标字段数量不一致："
                f"目标字段 {len(target_column_names)} 个，"
                f"SELECT 字段 "
                f"{len(branch_projections)} 个"
            )

        (
            branch_source_table_names,
            branch_source_by_qualifier,
        ) = _extract_source_tables(
            select_expression
        )

        # ====================================================
        # 按位置解析每个输出字段
        # ====================================================

        for ordinal_position, (
            target_column_name,
            projection_expression,
        ) in enumerate(
            zip(
                target_column_names,
                branch_projections,
            ),
            start=1,
        ):
            (
                source_fields,
                expression_text,
                relation_type,
            ) = _extract_projection_sources(
                projection_expression=(
                    projection_expression
                ),
                dialect=render_dialect,
                source_table_full_names=(
                    branch_source_table_names
                ),
                source_table_by_qualifier=(
                    branch_source_by_qualifier
                ),
            )

            # 清理 AS 输出别名。
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

            expression_text = (
                lineage_expression.sql(
                    dialect=render_dialect
                )
            )

            if not source_fields:
                raise UnsupportedColumnLineageError(
                    "CTE + UNION ALL "
                    "没有找到字段来源："
                    f"第 {branch_number} 个 SELECT，"
                    f"目标字段 {target_column_name}"
                )

            # 将 UNION 分支中的 CTE 字段展开成物理字段。
            (
                source_fields,
                expression_text,
                relation_type,
            ) = _expand_cte_source_fields(
                current_cte_name=(
                    "UNION ALL 第 "
                    f"{branch_number} 个分支"
                ),
                source_fields=source_fields,
                expression_text=expression_text,
                relation_type=relation_type,
                cte_name_by_lower=(
                    cte_name_by_lower
                ),
                cte_output_lineage=(
                    cte_output_lineage
                ),
            )

            if not source_fields:
                raise UnsupportedColumnLineageError(
                    "CTE + UNION ALL "
                    "没有找到物理来源字段："
                    f"第 {branch_number} 个 SELECT，"
                    f"目标字段 {target_column_name}"
                )

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

    unique_mappings = tuple(
        dict.fromkeys(
            mappings
        )
    )

    physical_source_table_names = tuple(
        dict.fromkeys(
            mapping.source_table_full_name
            for mapping in unique_mappings
        )
    )

    return DirectColumnLineageResult(
        dialect=normalized_dialect,
        source_table_full_names=(
            physical_source_table_names
        ),
        target_table_full_name=(
            target_table_full_name
        ),
        mappings=unique_mappings,
    )

def _extract_union_all_lineage(
    *,
    union_expression: exp.Union,
    target_table_full_name: str,
    target_column_names: tuple[str, ...],
    normalized_dialect: str,
    render_dialect: str | None,
) -> DirectColumnLineageResult:
    """
    提取 UNION ALL 的字段血缘。

    UNION ALL 按字段位置对齐。

    例如：

        INSERT INTO target_table (
            id,
            amount
        )

        SELECT
            web_id,
            web_amount
        FROM web_table

        UNION ALL

        SELECT
            store_id,
            store_amount
        FROM store_table

    位置映射：

        第一个分支第1列 web_id
            -> target_table.id

        第二个分支第1列 store_id
            -> target_table.id

        第一个分支第2列 web_amount
            -> target_table.amount

        第二个分支第2列 store_amount
            -> target_table.amount
    """

    select_branches = (
        _flatten_union_all_selects(
            union_expression
        )
    )

    if len(select_branches) < 2:
        raise UnsupportedColumnLineageError(
            "UNION ALL 至少需要两个 SELECT 分支"
        )

    mappings: list[
        DirectColumnMapping
    ] = []

    all_source_table_full_names: list[str] = []

    # ========================================================
    # 逐个解析 UNION ALL 的 SELECT 分支
    # ========================================================

    for branch_number, select_expression in enumerate(
        select_branches,
        start=1,
    ):
        _validate_simple_select(
            select_expression
        )

        branch_projections = tuple(
            select_expression.expressions
        )

        # 每个分支的字段数量都必须和
        # INSERT 目标字段数量相等。
        if len(branch_projections) != len(
            target_column_names
        ):
            raise UnsupportedColumnLineageError(
                "UNION ALL 第 "
                f"{branch_number} 个 SELECT "
                "字段数量与 INSERT 目标字段数量不一致："
                f"目标字段 {len(target_column_names)} 个，"
                f"SELECT 字段 "
                f"{len(branch_projections)} 个"
            )

        (
            source_table_full_names,
            source_table_by_qualifier,
        ) = _extract_source_tables(
            select_expression
        )

        all_source_table_full_names.extend(
            source_table_full_names
        )

        # ====================================================
        # 按字段位置建立映射
        # ====================================================

        for ordinal_position, (
            target_column_name,
            projection_expression,
        ) in enumerate(
            zip(
                target_column_names,
                branch_projections,
            ),
            start=1,
        ):
            (
                source_fields,
                expression_text,
                relation_type,
            ) = _extract_projection_sources(
                projection_expression=(
                    projection_expression
                ),
                dialect=render_dialect,
                source_table_full_names=(
                    source_table_full_names
                ),
                source_table_by_qualifier=(
                    source_table_by_qualifier
                ),
            )
            # UNION ALL 分支的投影字段可能带有别名。
            #
            # 例如：
            #
            #     price * quantity AS amount
            #
            # expression_text 只保存实际计算表达式：
            #
            #     price * quantity
            #
            # 不保存输出别名：
            #
            #     AS amount
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
                    "UNION ALL 当前阶段要求每个输出字段"
                    "都有明确的物理来源字段："
                    f"第 {branch_number} 个 SELECT，"
                    f"目标字段 {target_column_name}"
                )

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

    # 保持来源表第一次出现时的顺序，同时去重。
    unique_source_table_full_names = tuple(
        dict.fromkeys(
            all_source_table_full_names
        )
    )

    # 完全相同的映射只保留一份。
    #
    # 正常情况下不同 UNION ALL 分支来源表不同，
    # 不会被去重。
    unique_mappings = tuple(
        dict.fromkeys(
            mappings
        )
    )

    return DirectColumnLineageResult(
        dialect=normalized_dialect,
        source_table_full_names=(
            unique_source_table_full_names
        ),
        target_table_full_name=(
            target_table_full_name
        ),
        mappings=unique_mappings,
    )
def _build_direct_select_source_context(
    select_expression: exp.Select,
) -> tuple[
    tuple[str, ...],
    dict[str, str],
]:
    """
    构建当前 SELECT 直接来源表的上下文。

    只处理当前 SELECT 的 FROM 和 JOIN，
    不递归进入 CTE 定义内部。

    例如：

        SELECT
            o.amount - r.refund_amount
        FROM order_base AS o
        JOIN refund_base AS r
          ON o.order_id = r.order_id

    返回来源名称：

        (
            "order_base",
            "refund_base",
        )

    返回限定符映射：

        {
            "order_base": "order_base",
            "o": "order_base",
            "refund_base": "refund_base",
            "r": "refund_base",
        }
    """

    direct_tables = (
        _extract_direct_select_tables(
            select_expression
        )
    )

    source_table_full_names: list[str] = []

    source_table_by_qualifier: dict[
        str,
        str,
    ] = {}

    for table_expression in direct_tables:
        table_full_name = (
            _table_full_name(
                table_expression
            )
        )

        source_table_full_names.append(
            table_full_name
        )

        qualifiers = {
            table_full_name,
            table_expression.name,
        }

        table_alias = (
            table_expression.alias
        )

        if table_alias:
            qualifiers.add(
                table_alias
            )

        for qualifier in qualifiers:
            if not qualifier:
                continue

            source_table_by_qualifier[
                qualifier
            ] = table_full_name

            source_table_by_qualifier[
                qualifier.lower()
            ] = table_full_name

    return (
        tuple(
            dict.fromkeys(
                source_table_full_names
            )
        ),
        source_table_by_qualifier,
    )
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
                    f"{output_name} 没有找到来源字段"
                )

            # --------------------------------------------------------
            # 如果来源字段指向上游 CTE，
            # 在这里展开成最终物理表字段。
            # --------------------------------------------------------

            (
                source_fields,
                expression_text,
                relation_type,
            ) = _expand_cte_source_fields(
                current_cte_name=cte_name,
                source_fields=source_fields,
                expression_text=expression_text,
                relation_type=relation_type,
                cte_name_by_lower=cte_name_by_lower,
                cte_output_lineage=cte_output_lineage,
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
    (
        final_source_table_full_names,
        final_source_table_by_qualifier,
    ) = _build_direct_select_source_context(
        select_expression
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

        if not isinstance(
                final_expression,
                exp.Column,
        ):
            # ========================================================
            # 最终 SELECT 中发生了新的计算。
            #
            # 例如：
            #
            #     SUM(amount) AS total_amount
            #
            # 或：
            #
            #     o.amount - r.refund_amount AS net_amount
            #
            # 先提取当前表达式直接引用的 CTE 字段，
            # 再把这些 CTE 字段展开到最终物理来源。
            # ========================================================

            (
                source_fields,
                expression_text,
                relation_type,
            ) = _extract_projection_sources(
                projection_expression=(
                    projection_expression
                ),
                dialect=render_dialect,
                source_table_full_names=(
                    final_source_table_full_names
                ),
                source_table_by_qualifier=(
                    final_source_table_by_qualifier
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
                    "CTE 最终 SELECT 计算字段"
                    "没有找到来源："
                    f"{target_column_name}"
                )

            (
                source_fields,
                expression_text,
                relation_type,
            ) = _expand_cte_source_fields(
                current_cte_name=(
                    "最终 SELECT"
                ),
                source_fields=source_fields,
                expression_text=expression_text,
                relation_type=relation_type,
                cte_name_by_lower=(
                    cte_name_by_lower
                ),
                cte_output_lineage=(
                    cte_output_lineage
                ),
            )

            if not source_fields:
                raise UnsupportedColumnLineageError(
                    "CTE 最终 SELECT 计算字段"
                    "没有找到物理来源："
                    f"{target_column_name}"
                )

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

            # 当前投影已经完成处理，
            # 不再进入下面的直接 Column 处理逻辑。
            continue

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

def _get_select_from_source(
    select_expression: exp.Select,
) -> exp.Expression:
    """
    取得 SELECT 的直接 FROM 来源。

    支持返回：

        exp.Table
        exp.Subquery

    例如：

        SELECT *
        FROM ods.orders

    返回：

        ods.orders

    又例如：

        SELECT *
        FROM (
            SELECT *
            FROM ods.orders
        ) AS order_base

    返回整个 Subquery。
    """

    from_expression = (
        select_expression.args.get("from_")
        or select_expression.args.get("from")
    )

    if from_expression is None:
        raise UnsupportedColumnLineageError(
            "SELECT 没有 FROM 数据源"
        )

    source_expression = (
        from_expression.this
    )

    if source_expression is None:
        raise UnsupportedColumnLineageError(
            "SELECT 的 FROM 数据源为空"
        )

    return source_expression

def _expand_subquery_source_fields(
    *,
    subquery_alias: str,
    source_fields: tuple[
        tuple[str, str],
        ...,
    ],
    expression_text: str,
    relation_type: str,
    subquery_output_lineage: dict[
        str,
        dict[str, object],
    ],
) -> tuple[
    tuple[tuple[str, str], ...],
    str,
    str,
]:
    """
    把子查询输出字段展开成物理来源字段。

    例如子查询：

        SELECT
            price * quantity AS amount
        FROM ods.orders

    外层：

        SELECT
            s.amount
        FROM (...) AS s

    外层最初识别到：

        s.amount

    展开后变成：

        ods.orders.price
        ods.orders.quantity

    并继承：

        relation_type = transform
        expression_text = price * quantity
    """

    expanded_source_fields: list[
        tuple[str, str]
    ] = []

    upstream_relation_types: list[str] = []

    inherited_expression_text: str | None = None

    normalized_subquery_alias = (
        subquery_alias.lower()
    )

    for (
        source_table_full_name,
        source_column_name,
    ) in source_fields:
        source_simple_name = (
            source_table_full_name
            .split(".")[-1]
            .lower()
        )

        if (
            source_simple_name
            != normalized_subquery_alias
        ):
            raise UnsupportedColumnLineageError(
                "子查询外层 SELECT 引用了无法识别的"
                "数据源："
                f"{source_table_full_name}"
            )

        upstream_output = (
            subquery_output_lineage.get(
                source_column_name.lower()
            )
        )

        if upstream_output is None:
            raise UnsupportedColumnLineageError(
                f"子查询 {subquery_alias} "
                f"没有输出字段：{source_column_name}"
            )

        upstream_source_fields = (
            upstream_output[
                "source_fields"
            ]
        )

        upstream_expression_text = (
            upstream_output[
                "expression_text"
            ]
        )

        upstream_relation_type = (
            upstream_output[
                "relation_type"
            ]
        )

        expanded_source_fields.extend(
            upstream_source_fields
        )

        upstream_relation_types.append(
            str(upstream_relation_type)
        )

        # 外层只是直接引用一个子查询字段时，
        # 继承子查询内部真正的计算表达式。
        if (
            relation_type == "direct"
            and len(source_fields) == 1
        ):
            inherited_expression_text = str(
                upstream_expression_text
            )

    deduplicated_source_fields = tuple(
        dict.fromkeys(
            expanded_source_fields
        )
    )

    merged_relation_type = (
        _merge_cte_relation_type(
            current_relation_type=relation_type,
            upstream_relation_types=tuple(
                upstream_relation_types
            ),
        )
    )

    if (
        relation_type == "direct"
        and len(source_fields) == 1
        and inherited_expression_text is not None
    ):
        final_expression_text = (
            inherited_expression_text
        )
    else:
        final_expression_text = expression_text

    return (
        deduplicated_source_fields,
        final_expression_text,
        merged_relation_type,
    )

def _resolve_subquery_select_outputs(
    *,
    select_expression: exp.Select,
    render_dialect: str | None,
) -> tuple[
    dict[str, dict[str, object]],
    tuple[str, ...],
]:
    """
    递归解析一个 SELECT 的输出字段。

    返回：

        1. 输出字段血缘字典；
        2. 最终物理来源表。

    支持：

        FROM 物理表

        FROM (
            SELECT ...
            FROM 物理表
        ) AS subquery_alias

        FROM (
            SELECT ...
            FROM (
                SELECT ...
                FROM 物理表
            ) AS level_one
        ) AS level_two
    """

    source_expression = (
        _get_select_from_source(
            select_expression
        )
    )

    output_by_name: dict[
        str,
        dict[str, object],
    ] = {}

    # ========================================================
    # 情况一：当前 SELECT 直接读取物理表
    # ========================================================

    if isinstance(
        source_expression,
        exp.Table,
    ):
        _validate_simple_select(
            select_expression
        )

        (
            source_table_full_names,
            source_table_by_qualifier,
        ) = _extract_source_tables(
            select_expression
        )

        for projection_expression in (
            select_expression.expressions
        ):
            output_name = (
                _get_projection_output_name(
                    projection_expression
                )
            )

            normalized_output_name = (
                output_name.lower()
            )

            if (
                normalized_output_name
                in output_by_name
            ):
                raise UnsupportedColumnLineageError(
                    "子查询存在重复输出字段："
                    f"{output_name}"
                )

            (
                source_fields,
                expression_text,
                relation_type,
            ) = _extract_projection_sources(
                projection_expression=(
                    projection_expression
                ),
                dialect=render_dialect,
                source_table_full_names=(
                    source_table_full_names
                ),
                source_table_by_qualifier=(
                    source_table_by_qualifier
                ),
            )

            # 去掉 AS 输出别名。
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

            expression_text = (
                lineage_expression.sql(
                    dialect=render_dialect
                )
            )

            if not source_fields:
                raise UnsupportedColumnLineageError(
                    "子查询输出字段没有找到物理来源："
                    f"{output_name}"
                )

            output_by_name[
                normalized_output_name
            ] = {
                "source_fields": source_fields,
                "expression_text": expression_text,
                "relation_type": relation_type,
            }

        return (
            output_by_name,
            tuple(
                dict.fromkeys(
                    source_table_full_names
                )
            ),
        )

    # ========================================================
    # 情况二：当前 SELECT 读取另一个子查询
    # ========================================================

    if isinstance(
        source_expression,
        exp.Subquery,
    ):
        # 当前阶段，一个子查询外层不能继续 JOIN 其他表。
        if select_expression.args.get("joins"):
            raise UnsupportedColumnLineageError(
                "当前阶段暂不支持 FROM 子查询后继续 JOIN"
            )

        subquery_alias = (
            source_expression.alias_or_name
        )

        if not subquery_alias:
            raise UnsupportedColumnLineageError(
                "FROM 子查询必须定义别名"
            )

        inner_expression = (
            _unwrap_insert_source_expression(
                source_expression.this
            )
        )

        if not isinstance(
            inner_expression,
            exp.Select,
        ):
            raise UnsupportedColumnLineageError(
                "当前阶段子查询内部必须是 SELECT"
            )

        (
            inner_output_lineage,
            physical_source_table_names,
        ) = _resolve_subquery_select_outputs(
            select_expression=inner_expression,
            render_dialect=render_dialect,
        )

        # 外层 SELECT 把子查询别名当作直接来源。
        subquery_source_table_names = (
            subquery_alias,
        )

        subquery_source_by_qualifier = {
            subquery_alias: subquery_alias,
            subquery_alias.lower(): (
                subquery_alias
            ),
        }

        for projection_expression in (
            select_expression.expressions
        ):
            output_name = (
                _get_projection_output_name(
                    projection_expression
                )
            )

            normalized_output_name = (
                output_name.lower()
            )

            if (
                normalized_output_name
                in output_by_name
            ):
                raise UnsupportedColumnLineageError(
                    "子查询外层存在重复输出字段："
                    f"{output_name}"
                )

            (
                source_fields,
                expression_text,
                relation_type,
            ) = _extract_projection_sources(
                projection_expression=(
                    projection_expression
                ),
                dialect=render_dialect,
                source_table_full_names=(
                    subquery_source_table_names
                ),
                source_table_by_qualifier=(
                    subquery_source_by_qualifier
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

            expression_text = (
                lineage_expression.sql(
                    dialect=render_dialect
                )
            )

            if not source_fields:
                raise UnsupportedColumnLineageError(
                    "子查询外层字段没有找到来源："
                    f"{output_name}"
                )

            (
                source_fields,
                expression_text,
                relation_type,
            ) = _expand_subquery_source_fields(
                subquery_alias=subquery_alias,
                source_fields=source_fields,
                expression_text=expression_text,
                relation_type=relation_type,
                subquery_output_lineage=(
                    inner_output_lineage
                ),
            )

            if not source_fields:
                raise UnsupportedColumnLineageError(
                    "子查询字段没有找到最终物理来源："
                    f"{output_name}"
                )

            output_by_name[
                normalized_output_name
            ] = {
                "source_fields": source_fields,
                "expression_text": expression_text,
                "relation_type": relation_type,
            }

        return (
            output_by_name,
            physical_source_table_names,
        )

    raise UnsupportedColumnLineageError(
        "当前阶段 FROM 只支持物理表或子查询"
    )
def _extract_subquery_lineage(
    *,
    select_expression: exp.Select,
    target_table_full_name: str,
    target_column_names: tuple[str, ...],
    normalized_dialect: str,
    render_dialect: str | None,
) -> DirectColumnLineageResult:
    """
    提取 INSERT ... SELECT FROM (...) 子查询字段血缘。
    """

    (
        output_lineage,
        physical_source_table_names,
    ) = _resolve_subquery_select_outputs(
        select_expression=select_expression,
        render_dialect=render_dialect,
    )

    output_items = tuple(
        output_lineage.values()
    )

    if len(output_items) != len(
        target_column_names
    ):
        raise UnsupportedColumnLineageError(
            "INSERT 目标字段数量和子查询最终 "
            "SELECT 字段数量不一致："
            f"目标字段 {len(target_column_names)} 个，"
            f"SELECT 字段 {len(output_items)} 个"
        )

    mappings: list[
        DirectColumnMapping
    ] = []

    for ordinal_position, (
        target_column_name,
        output_item,
    ) in enumerate(
        zip(
            target_column_names,
            output_items,
        ),
        start=1,
    ):
        source_fields = output_item[
            "source_fields"
        ]

        expression_text = str(
            output_item[
                "expression_text"
            ]
        )

        relation_type = str(
            output_item[
                "relation_type"
            ]
        )

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

    unique_mappings = tuple(
        dict.fromkeys(
            mappings
        )
    )

    return DirectColumnLineageResult(
        dialect=normalized_dialect,
        source_table_full_names=tuple(
            dict.fromkeys(
                physical_source_table_names
            )
        ),
        target_table_full_name=(
            target_table_full_name
        ),
        mappings=unique_mappings,
    )
def _extract_create_target(
    create_expression: exp.Create,
) -> tuple[
    exp.Table,
    tuple[str, ...],
]:
    """
    解析 CREATE TABLE 或 CREATE VIEW 的目标。

    支持：

        CREATE TABLE dwd.orders AS ...

        CREATE VIEW ads.order_view AS ...

        CREATE VIEW ads.order_view (
            order_id,
            amount
        ) AS ...

    返回：

        目标表表达式
        显式声明的目标字段

    如果 CREATE 没有显式字段列表，
    第二个返回值为空，后面从 SELECT 推导。
    """

    create_kind = str(
        create_expression.args.get("kind")
        or ""
    ).upper()

    if create_kind not in {
        "TABLE",
        "VIEW",
    }:
        raise UnsupportedColumnLineageError(
            "当前 CREATE 血缘解析只支持 "
            "CREATE TABLE 和 CREATE VIEW"
        )

    target_expression = (
        create_expression.this
    )

    explicit_column_names: tuple[
        str,
        ...,
    ] = ()

    # 例如：
    #
    # CREATE VIEW ads.order_view (
    #     order_id,
    #     amount
    # ) AS ...
    #
    # SQLGlot 可能把目标表示为 Schema。
    if isinstance(
        target_expression,
        exp.Schema,
    ):
        table_expression = (
            target_expression.this
        )

        explicit_column_names = tuple(
            column_expression.name
            for column_expression
            in target_expression.expressions
        )

    else:
        table_expression = (
            target_expression
        )

    if not isinstance(
        table_expression,
        exp.Table,
    ):
        raise UnsupportedColumnLineageError(
            "CREATE 没有找到合法的目标表"
        )

    if explicit_column_names:
        if any(
            not column_name
            for column_name
            in explicit_column_names
        ):
            raise UnsupportedColumnLineageError(
                "CREATE 目标字段名称不能为空"
            )

        if (
            len(set(explicit_column_names))
            != len(explicit_column_names)
        ):
            raise UnsupportedColumnLineageError(
                "CREATE 存在重复目标字段"
            )

    return (
        table_expression,
        explicit_column_names,
    )
def _get_create_output_projections(
    source_expression: exp.Expression,
) -> tuple[exp.Expression, ...]:
    """
    取得 CREATE AS 查询最外层的输出字段。

    普通 SELECT：

        SELECT order_id, amount
        FROM ...

    返回最外层两个投影字段。

    UNION ALL：

        SELECT order_id, amount FROM a
        UNION ALL
        SELECT order_id, amount FROM b

    目标字段名称由第一个 SELECT 分支决定。
    """

    source_expression = (
        _unwrap_insert_source_expression(
            source_expression
        )
    )

    if isinstance(
        source_expression,
        exp.Select,
    ):
        return tuple(
            source_expression.expressions
        )

    if isinstance(
        source_expression,
        exp.Union,
    ):
        select_branches = (
            _flatten_union_all_selects(
                source_expression
            )
        )

        if not select_branches:
            raise UnsupportedColumnLineageError(
                "CREATE UNION ALL 没有 SELECT 分支"
            )

        return tuple(
            select_branches[0].expressions
        )

    raise UnsupportedColumnLineageError(
        "CREATE AS 后面必须是 SELECT "
        "或 UNION ALL"
    )
def _infer_create_target_columns(
    *,
    create_expression: exp.Create,
    explicit_column_names: tuple[str, ...],
) -> tuple[str, ...]:
    """
    推导 CREATE TABLE/VIEW 的目标字段。

    优先级：

        1. CREATE 显式字段列表；
        2. SELECT 输出字段名称；
        3. SELECT 表达式的 AS 别名。

    例如：

        SELECT order_id

    推导：

        order_id

    例如：

        SELECT price * quantity AS amount

    推导：

        amount

    不允许：

        SELECT price * quantity

    因为没有明确目标字段名。
    """

    source_expression = (
        create_expression.expression
    )

    if source_expression is None:
        raise UnsupportedColumnLineageError(
            "CREATE TABLE/VIEW 没有 AS 查询"
        )

    projections = (
        _get_create_output_projections(
            source_expression
        )
    )

    if not projections:
        raise UnsupportedColumnLineageError(
            "CREATE AS SELECT 没有输出字段"
        )

    # CREATE 已经显式声明字段时，
    # 优先使用显式字段。
    if explicit_column_names:
        if len(explicit_column_names) != len(
            projections
        ):
            raise UnsupportedColumnLineageError(
                "CREATE 显式目标字段数量和 "
                "SELECT 输出字段数量不一致："
                f"目标字段 {len(explicit_column_names)} 个，"
                f"SELECT 字段 {len(projections)} 个"
            )

        return explicit_column_names

    inferred_column_names: list[str] = []

    for projection_expression in projections:
        # SELECT * 不能推导具体目标字段。
        if isinstance(
            projection_expression,
            exp.Star,
        ):
            raise UnsupportedColumnLineageError(
                "CREATE AS SELECT * "
                "无法推导目标字段"
            )

        if (
            isinstance(
                projection_expression,
                exp.Column,
            )
            and projection_expression.is_star
        ):
            raise UnsupportedColumnLineageError(
                "CREATE AS SELECT table.* "
                "无法推导目标字段"
            )

        output_name = (
            _get_projection_output_name(
                projection_expression
            )
        )

        if not output_name:
            raise UnsupportedColumnLineageError(
                "CREATE SELECT 输出字段名称不能为空"
            )

        if output_name == "*":
            raise UnsupportedColumnLineageError(
                "CREATE AS SELECT * "
                "无法推导目标字段"
            )

        inferred_column_names.append(
            output_name
        )

    if (
        len(set(inferred_column_names))
        != len(inferred_column_names)
    ):
        raise UnsupportedColumnLineageError(
            "CREATE SELECT 存在重复输出字段名称"
        )

    return tuple(
        inferred_column_names
    )
def _extract_create_lineage(
    *,
    create_expression: exp.Create,
    normalized_dialect: str,
    render_dialect: str | None,
) -> DirectColumnLineageResult:
    """
    将 CREATE TABLE/VIEW AS SELECT 转换成内部 INSERT，
    然后复用现有字段血缘解析器。

    这样能够自动复用：

        普通 SELECT
        JOIN
        CASE WHEN
        聚合
        CTE
        UNION ALL
        FROM 子查询
    """

    (
        target_table_expression,
        explicit_column_names,
    ) = _extract_create_target(
        create_expression
    )

    target_column_names = (
        _infer_create_target_columns(
            create_expression=create_expression,
            explicit_column_names=(
                explicit_column_names
            ),
        )
    )

    source_expression = (
        create_expression.expression
    )

    if source_expression is None:
        raise UnsupportedColumnLineageError(
            "CREATE TABLE/VIEW 没有 AS 查询"
        )

    target_table_sql = (
        target_table_expression.sql(
            dialect=render_dialect
        )
    )

    target_column_sql = ", ".join(
        exp.to_identifier(
            column_name
        ).sql(
            dialect=render_dialect
        )
        for column_name
        in target_column_names
    )

    source_sql = source_expression.sql(
        dialect=render_dialect
    )

    synthetic_insert_sql = (
        f"INSERT INTO {target_table_sql} "
        f"({target_column_sql}) "
        f"{source_sql}"
    )

    # 复用现有 INSERT 字段血缘解析器。
    return extract_direct_column_lineage(
        sql_text=synthetic_insert_sql,
        dialect=normalized_dialect,
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
    # ============================================================
    # CREATE TABLE/VIEW AS SELECT
    # ============================================================

    if isinstance(
            expression,
            exp.Create,
    ):
        return _extract_create_lineage(
            create_expression=expression,
            normalized_dialect=(
                normalized_dialect
            ),
            render_dialect=read_dialect,
        )

    if not isinstance(
        expression,
        exp.Insert,
    ):
        raise UnsupportedColumnLineageError(
            "当前只支持 INSERT INTO、"
            "CREATE TABLE AS SELECT 和 "
            "CREATE VIEW AS SELECT"
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
    # ============================================================
    # UNION ALL 单独处理
    # ============================================================

    insert_source_expression = (
        expression.expression
    )

    if insert_source_expression is None:
        raise UnsupportedColumnLineageError(
            "INSERT 没有找到数据来源"
        )

    insert_source_expression = (
        _unwrap_insert_source_expression(
            insert_source_expression
        )
    )

    if isinstance(
            insert_source_expression,
            exp.Union,
    ):
        union_with_expression = (
            _find_union_with_expression(
                insert_expression=expression,
                union_expression=(
                    insert_source_expression
                ),
            )
        )

        # 有 WITH：
        #
        #     CTE + UNION ALL
        if union_with_expression is not None:
            return _extract_cte_union_all_lineage(
                union_expression=(
                    insert_source_expression
                ),
                with_expression=(
                    union_with_expression
                ),
                target_table_full_name=(
                    target_table_full_name
                ),
                target_column_names=(
                    target_column_names
                ),
                normalized_dialect=(
                    normalized_dialect
                ),
                render_dialect=read_dialect,
            )

        # 没有 WITH：
        #
        #     普通物理表 UNION ALL
        return _extract_union_all_lineage(
            union_expression=(
                insert_source_expression
            ),
            target_table_full_name=(
                target_table_full_name
            ),
            target_column_names=(
                target_column_names
            ),
            normalized_dialect=(
                normalized_dialect
            ),
            render_dialect=read_dialect,
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
    # ============================================================
    # FROM 子查询单独处理
    # ============================================================

    direct_from_source = (
        _get_select_from_source(
            select_expression
        )
    )

    if isinstance(
            direct_from_source,
            exp.Subquery,
    ):
        return _extract_subquery_lineage(
            select_expression=select_expression,
            target_table_full_name=(
                target_table_full_name
            ),
            target_column_names=(
                target_column_names
            ),
            normalized_dialect=(
                normalized_dialect
            ),
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
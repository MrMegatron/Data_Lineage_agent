from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlglot import exp

from app.services.sql_parser import (
    ParsedSqlStatement,
    SqlScriptParseResult,
)


# ============================================================
# 1. 数据表引用
# ============================================================

@dataclass(frozen=True, slots=True)
class TableReference:
    """
    SQL 中识别出来的一张数据表。

    支持：

        table_name
        schema_name.table_name
        catalog_name.schema_name.table_name
    """

    catalog_name: str | None
    schema_name: str | None
    table_name: str
    full_name: str


# ============================================================
# 2. 表访问结果
# ============================================================

@dataclass(frozen=True, slots=True)
class ExtractedTableAccess:
    """
    一条 SQL 对一张表的访问记录。

    后续会转换成：

        DataTable
        ScriptTableAccess
    """

    statement_no: int

    # read 或 write
    access_type: str

    catalog_name: str | None
    schema_name: str | None
    table_name: str
    full_name: str

    line_start: int
    line_end: int

    # 当前先保存整条 SQL 作为证据
    evidence_sql: str


# ============================================================
# 3. 标准化一个标识符
# ============================================================

def _normalize_identifier(
    identifier: Any,
) -> str | None:
    """
    标准化 catalog、schema 和 table 名称。

    规则：

    1. 未加引号的标识符转换为小写；
    2. 加引号的标识符保留原始大小写；
    3. 没有标识符时返回 None。

    示例：

        ORDERS
            -> orders

        "OrderDetail"
            -> OrderDetail
    """

    if identifier is None:
        return None

    if isinstance(identifier, exp.Identifier):

        value = str(identifier.this)

        if identifier.args.get("quoted"):
            return value

        return value.lower()

    # 某些 SQLGlot 节点可能通过 name 属性提供名称。
    name = getattr(identifier, "name", None)

    if name:
        return str(name).lower()

    value = str(identifier).strip()

    if not value:
        return None

    return value.lower()


# ============================================================
# 4. 将 SQLGlot Table 节点转换成表引用
# ============================================================

def _build_table_reference(
    table_expression: exp.Table,
) -> TableReference | None:
    """
    从 SQLGlot Table 节点提取：

        catalog
        schema
        table
        full_name
    """

    catalog_name = _normalize_identifier(
        table_expression.args.get("catalog")
    )

    schema_name = _normalize_identifier(
        table_expression.args.get("db")
    )

    table_name = _normalize_identifier(
        table_expression.args.get("this")
    )

    if not table_name:
        return None

    full_name_parts = [
        name
        for name in (
            catalog_name,
            schema_name,
            table_name,
        )
        if name
    ]

    full_name = ".".join(
        full_name_parts
    )

    return TableReference(
        catalog_name=catalog_name,
        schema_name=schema_name,
        table_name=table_name,
        full_name=full_name,
    )


# ============================================================
# 5. 找出写入目标表节点
# ============================================================

def _get_write_target_tables(
    expression: Any,
) -> list[exp.Table]:
    """
    查找当前 SQL 的写入目标表。

    第一版支持：

    - INSERT
    - CREATE TABLE / VIEW
    - UPDATE
    - DELETE
    - MERGE

    对于：

        INSERT INTO target_table
        SELECT * FROM source_table

    只返回：

        target_table
    """

    write_expression_types = tuple(
        expression_type
        for expression_type in (
            getattr(exp, "Insert", None),
            getattr(exp, "Create", None),
            getattr(exp, "Update", None),
            getattr(exp, "Delete", None),
            getattr(exp, "Merge", None),
        )
        if expression_type is not None
    )

    if not isinstance(
        expression,
        write_expression_types,
    ):
        return []

    target_expression = expression.args.get(
        "this"
    )

    if target_expression is None:
        return []

    if isinstance(
        target_expression,
        exp.Table,
    ):
        return [target_expression]

    # INSERT INTO table_name (col1, col2)
    #
    # SQLGlot 中目标可能是 Schema 节点，
    # Table 会位于 Schema 内部。
    nested_table = target_expression.find(
        exp.Table
    )

    if nested_table is None:
        return []

    return [nested_table]


# ============================================================
# 6. 收集 CTE 名称
# ============================================================

def _get_cte_names(
    expression: Any,
) -> set[str]:
    """
    收集当前 SQL 中定义的所有 CTE 名称。

    例如：

        WITH recent_orders AS (...)

    返回：

        {"recent_orders"}
    """

    cte_names: set[str] = set()

    for cte_expression in expression.find_all(
        exp.CTE
    ):
        alias_name = (
            cte_expression.alias_or_name
        )

        if alias_name:
            cte_names.add(
                str(alias_name).casefold()
            )

    return cte_names


# ============================================================
# 7. 判断 Table 节点是不是 CTE 引用
# ============================================================

def _is_cte_reference(
    table_reference: TableReference,
    cte_names: set[str],
) -> bool:
    """
    只有没有 catalog 和 schema 的单段表名，
    才可能被当成 CTE。

    例如：

        recent_orders
            可能是 CTE

        ods.recent_orders
            是物理表，不应当因为名字相同而删除
    """

    if table_reference.catalog_name:
        return False

    if table_reference.schema_name:
        return False

    return (
        table_reference.table_name.casefold()
        in cte_names
    )


# ============================================================
# 8. 去除同类重复访问
# ============================================================

def _deduplicate_accesses(
    accesses: list[ExtractedTableAccess],
) -> tuple[ExtractedTableAccess, ...]:
    """
    同一条 SQL 中，同一张表可能出现多次。

    例如：

        SELECT *
        FROM ods.orders a
        JOIN ods.orders b
          ON a.parent_id = b.order_id

    在数据库中只保存一条：

        READ ods.orders

    但是 READ 和 WRITE 不会互相去重。
    """

    unique_accesses: list[
        ExtractedTableAccess
    ] = []

    seen_keys: set[
        tuple[int, str, str]
    ] = set()

    for access in accesses:

        key = (
            access.statement_no,
            access.access_type,
            access.full_name.casefold(),
        )

        if key in seen_keys:
            continue

        seen_keys.add(key)
        unique_accesses.append(access)

    return tuple(unique_accesses)


# ============================================================
# 9. 提取单条 SQL 的表访问
# ============================================================

def extract_statement_table_accesses(
    statement: ParsedSqlStatement,
) -> tuple[ExtractedTableAccess, ...]:
    """
    提取一条 SQL 的 READ / WRITE 表。

    解析失败的 SQL 不进行表提取，
    直接返回空元组。
    """

    if (
        statement.parse_status != "success"
        or statement.expression is None
    ):
        return ()

    expression = statement.expression

    write_target_tables = (
        _get_write_target_tables(
            expression
        )
    )

    # 使用对象 id 区分真正的目标 Table 节点。
    #
    # 不能只通过 full_name 排除，否则：
    #
    # INSERT INTO dwd.orders
    # SELECT * FROM dwd.orders
    #
    # 会错误地把 READ dwd.orders 删除。
    write_target_node_ids = {
        id(table_expression)
        for table_expression
        in write_target_tables
    }

    cte_names = _get_cte_names(
        expression
    )

    extracted_accesses: list[
        ExtractedTableAccess
    ] = []

    # --------------------------------------------------------
    # 1. 提取 READ 表
    # --------------------------------------------------------

    for table_expression in expression.find_all(
        exp.Table
    ):

        # 目标表节点不是 READ。
        if id(table_expression) in (
            write_target_node_ids
        ):
            continue

        table_reference = (
            _build_table_reference(
                table_expression
            )
        )

        if table_reference is None:
            continue

        # CTE 名称不是物理数据表。
        if _is_cte_reference(
            table_reference=table_reference,
            cte_names=cte_names,
        ):
            continue

        extracted_accesses.append(
            ExtractedTableAccess(
                statement_no=(
                    statement.statement_no
                ),
                access_type="read",
                catalog_name=(
                    table_reference.catalog_name
                ),
                schema_name=(
                    table_reference.schema_name
                ),
                table_name=(
                    table_reference.table_name
                ),
                full_name=(
                    table_reference.full_name
                ),
                line_start=statement.line_start,
                line_end=statement.line_end,
                evidence_sql=statement.source_sql,
            )
        )

    # --------------------------------------------------------
    # 2. 提取 WRITE 表
    # --------------------------------------------------------

    for table_expression in write_target_tables:

        table_reference = (
            _build_table_reference(
                table_expression
            )
        )

        if table_reference is None:
            continue

        extracted_accesses.append(
            ExtractedTableAccess(
                statement_no=(
                    statement.statement_no
                ),
                access_type="write",
                catalog_name=(
                    table_reference.catalog_name
                ),
                schema_name=(
                    table_reference.schema_name
                ),
                table_name=(
                    table_reference.table_name
                ),
                full_name=(
                    table_reference.full_name
                ),
                line_start=statement.line_start,
                line_end=statement.line_end,
                evidence_sql=statement.source_sql,
            )
        )

    return _deduplicate_accesses(
        extracted_accesses
    )


# ============================================================
# 10. 提取整个文件的表访问
# ============================================================

def extract_script_table_accesses(
    parse_result: SqlScriptParseResult,
) -> tuple[ExtractedTableAccess, ...]:
    """
    提取整个 SQL 文件中所有语句的表访问结果。

    解析失败的语句会被跳过，
    解析成功的语句继续处理。
    """

    all_accesses: list[
        ExtractedTableAccess
    ] = []

    for statement in parse_result.statements:

        statement_accesses = (
            extract_statement_table_accesses(
                statement
            )
        )

        all_accesses.extend(
            statement_accesses
        )

    return tuple(all_accesses)
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ColumnLineage,
    SourceScript,
)
from app.services.column_lineage_extractor import (
    UnsupportedColumnLineageError,
    extract_direct_column_lineage,
)
from app.services.column_lineage_persistence_service import (
    persist_direct_column_lineage,
)


# ============================================================
# 1. 单条 SQL 语句处理结果
# ============================================================

@dataclass(frozen=True)
class StatementColumnLineageResult:
    """
    一条 SQL 语句的字段血缘处理结果。

    status:

        success
            成功提取并保存字段血缘。

        skipped
            SQL 语法没有问题，但当前版本暂不支持。

        failed
            执行字段血缘持久化时发生业务错误。
    """

    statement_no: int
    status: str

    created_column_count: int
    reused_column_count: int

    created_lineage_count: int
    created_evidence_count: int

    reason: str | None


# ============================================================
# 2. 整个脚本处理结果
# ============================================================

@dataclass(frozen=True)
class ScriptColumnLineageResult:
    """
    一份 SQL 脚本的字段血缘处理汇总。
    """

    project_id: int
    script_id: int

    total_statement_count: int

    success_statement_count: int
    skipped_statement_count: int
    failed_statement_count: int

    deleted_lineage_count: int

    created_column_count: int
    reused_column_count: int

    created_lineage_count: int
    created_evidence_count: int

    statements: tuple[
        StatementColumnLineageResult,
        ...
    ]


# ============================================================
# 3. 查询脚本
# ============================================================

def _get_script_or_raise(
    db: Session,
    script_id: int,
) -> SourceScript:
    """
    查询需要处理的源代码脚本。
    """

    script = db.get(
        SourceScript,
        script_id,
    )

    if script is None:
        raise ValueError(
            f"源码脚本不存在：script_id={script_id}"
        )

    return script


# ============================================================
# 4. 解析脚本中的全部 SQL 语句
# ============================================================

def _parse_script_statements(
    source_code: str,
    dialect: str,
) -> tuple[
    exp.Expression,
    ...
]:
    """
    使用 SQLGlot 解析一份脚本中的全部 SQL 语句。

    与 parse_one 不同，sqlglot.parse 可以处理：

        SQL 语句1;
        SQL 语句2;
        SQL 语句3;
    """

    if not source_code.strip():
        return ()

    normalized_dialect = (
        dialect.strip().lower()
        if dialect
        else "unknown"
    )

    read_dialect = (
        None
        if normalized_dialect == "unknown"
        else normalized_dialect
    )

    try:
        expressions = sqlglot.parse(
            source_code,
            read=read_dialect,
        )

    except ParseError as exc:
        raise ValueError(
            f"SQL 脚本解析失败：{exc}"
        ) from exc

    # 某些空语句可能被解析成 None，
    # 因此这里统一过滤。
    return tuple(
        expression
        for expression in expressions
        if expression is not None
    )


# ============================================================
# 5. 删除脚本以前的全部字段血缘
# ============================================================

def _delete_script_lineages(
    db: Session,
    script_id: int,
) -> int:
    """
    删除当前脚本以前产生的字段血缘。

    为什么需要先删除：

    1. SQL 文件可能发生修改；
    2. 原来有三条语句，现在可能只有两条；
    3. 原来支持的语句，现在可能变成不支持；
    4. 不能让旧血缘残留在数据库中。

    对应的 LineageEvidence 会通过级联关系删除。
    """

    existing_lineages = list(
        db.scalars(
            select(ColumnLineage)
            .where(
                ColumnLineage.script_id
                == script_id
            )
        ).all()
    )

    for lineage in existing_lineages:
        db.delete(lineage)

    if existing_lineages:
        db.flush()

    return len(existing_lineages)


# ============================================================
# 6. 把表达式重新转换成 SQL
# ============================================================

def _expression_to_sql(
    expression: exp.Expression,
    dialect: str,
) -> str:
    """
    将 SQLGlot 表达式重新转换成 SQL 文本，
    再交给基础字段血缘提取器处理。
    """

    normalized_dialect = (
        dialect.strip().lower()
        if dialect
        else "unknown"
    )

    if normalized_dialect == "unknown":
        return expression.sql()

    return expression.sql(
        dialect=normalized_dialect
    )


# ============================================================
# 7. 处理整个脚本的字段血缘
# ============================================================

def process_script_column_lineage(
    db: Session,
    script_id: int,
) -> ScriptColumnLineageResult:
    """
    处理一份已经保存到 source_script 的 SQL 脚本。

    处理原则：

    1. 表级血缘和字段级血缘相互独立；
    2. 当前不支持的字段表达式只记为 skipped；
    3. 不因为字段血缘暂不支持而破坏表级血缘；
    4. 重复执行时删除旧血缘并重新生成；
    5. 本函数只 flush，不 commit。
    """

    script = _get_script_or_raise(
        db=db,
        script_id=script_id,
    )

    expressions = _parse_script_statements(
        source_code=script.source_code,
        dialect=script.dialect,
    )

    # 必须在 SQL 解析成功以后再删除旧结果。
    #
    # 如果脚本本身存在 SQL 语法错误，
    # _parse_script_statements 会先抛出异常，
    # 数据库中的旧字段血缘不会被提前删除。
    deleted_lineage_count = (
        _delete_script_lineages(
            db=db,
            script_id=script.id,
        )
    )

    statement_results: list[
        StatementColumnLineageResult
    ] = []

    total_created_column_count = 0
    total_reused_column_count = 0

    total_created_lineage_count = 0
    total_created_evidence_count = 0

    success_statement_count = 0
    skipped_statement_count = 0
    failed_statement_count = 0

    for statement_no, expression in enumerate(
        expressions,
        start=1,
    ):
        # ----------------------------------------------------
        # 非 INSERT 语句暂时跳过
        # ----------------------------------------------------

        if not isinstance(
            expression,
            exp.Insert,
        ):
            skipped_statement_count += 1

            statement_results.append(
                StatementColumnLineageResult(
                    statement_no=statement_no,
                    status="skipped",
                    created_column_count=0,
                    reused_column_count=0,
                    created_lineage_count=0,
                    created_evidence_count=0,
                    reason=(
                        "当前阶段只处理 "
                        "INSERT INTO ... SELECT ..."
                    ),
                )
            )

            continue

        statement_sql = _expression_to_sql(
            expression=expression,
            dialect=script.dialect,
        )

        # ----------------------------------------------------
        # 提取字段血缘
        # ----------------------------------------------------

        try:
            extraction_result = (
                extract_direct_column_lineage(
                    sql_text=statement_sql,
                    dialect=script.dialect,
                )
            )

        except UnsupportedColumnLineageError as exc:
            # SQL 合法，但当前阶段不支持。
            #
            # 例如：
            # SELECT *
            # JOIN
            # 聚合表达式
            # 计算表达式
            skipped_statement_count += 1

            statement_results.append(
                StatementColumnLineageResult(
                    statement_no=statement_no,
                    status="skipped",
                    created_column_count=0,
                    reused_column_count=0,
                    created_lineage_count=0,
                    created_evidence_count=0,
                    reason=str(exc),
                )
            )

            continue

        except ValueError as exc:
            # SQL 字段解析发生普通业务错误。
            failed_statement_count += 1

            statement_results.append(
                StatementColumnLineageResult(
                    statement_no=statement_no,
                    status="failed",
                    created_column_count=0,
                    reused_column_count=0,
                    created_lineage_count=0,
                    created_evidence_count=0,
                    reason=str(exc),
                )
            )

            continue

        # ----------------------------------------------------
        # 保存字段、字段血缘和证据
        # ----------------------------------------------------

        try:
            persistence_result = (
                persist_direct_column_lineage(
                    db=db,
                    project_id=script.project_id,
                    script_id=script.id,
                    statement_no=statement_no,
                    extraction_result=(
                        extraction_result
                    ),
                )
            )

        except ValueError as exc:
            # 例如表级血缘没有正确建立，
            # 导致来源表或者目标表不存在。
            failed_statement_count += 1

            statement_results.append(
                StatementColumnLineageResult(
                    statement_no=statement_no,
                    status="failed",
                    created_column_count=0,
                    reused_column_count=0,
                    created_lineage_count=0,
                    created_evidence_count=0,
                    reason=str(exc),
                )
            )

            continue

        success_statement_count += 1

        total_created_column_count += (
            persistence_result
            .created_column_count
        )

        total_reused_column_count += (
            persistence_result
            .reused_column_count
        )

        total_created_lineage_count += (
            persistence_result
            .created_lineage_count
        )

        total_created_evidence_count += (
            persistence_result
            .created_evidence_count
        )

        statement_results.append(
            StatementColumnLineageResult(
                statement_no=statement_no,
                status="success",
                created_column_count=(
                    persistence_result
                    .created_column_count
                ),
                reused_column_count=(
                    persistence_result
                    .reused_column_count
                ),
                created_lineage_count=(
                    persistence_result
                    .created_lineage_count
                ),
                created_evidence_count=(
                    persistence_result
                    .created_evidence_count
                ),
                reason=None,
            )
        )

    db.flush()

    return ScriptColumnLineageResult(
        project_id=script.project_id,
        script_id=script.id,
        total_statement_count=len(
            expressions
        ),
        success_statement_count=(
            success_statement_count
        ),
        skipped_statement_count=(
            skipped_statement_count
        ),
        failed_statement_count=(
            failed_statement_count
        ),
        deleted_lineage_count=(
            deleted_lineage_count
        ),
        created_column_count=(
            total_created_column_count
        ),
        reused_column_count=(
            total_reused_column_count
        ),
        created_lineage_count=(
            total_created_lineage_count
        ),
        created_evidence_count=(
            total_created_evidence_count
        ),
        statements=tuple(
            statement_results
        ),
    )
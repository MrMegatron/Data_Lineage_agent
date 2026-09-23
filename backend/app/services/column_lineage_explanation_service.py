from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    SourceScript,
)


# ============================================================
# 1. 来源字段
# ============================================================

@dataclass(
    frozen=True,
    slots=True,
)
class LineageExplanationSource:
    lineage_id: int

    source_column_id: int | None
    source_table_full_name: str | None
    source_column_name: str | None
    source_full_name: str | None

    relation_type: str
    expression_text: str | None


# ============================================================
# 2. 代码证据
# ============================================================

@dataclass(
    frozen=True,
    slots=True,
)
class LineageExplanationEvidence:
    evidence_id: int

    script_id: int
    file_name: str
    relative_path: str

    statement_no: int
    line_start: int | None
    line_end: int | None

    code_snippet: str
    expression_text: str | None


# ============================================================
# 3. 字段血缘解释结果
# ============================================================

@dataclass(
    frozen=True,
    slots=True,
)
class ColumnLineageExplanation:
    target_column_id: int
    target_table_full_name: str
    target_column_name: str
    target_full_name: str

    has_confirmed_lineage: bool

    natural_language: str
    pseudocode: str

    sources: tuple[
        LineageExplanationSource,
        ...,
    ]

    evidences: tuple[
        LineageExplanationEvidence,
        ...,
    ]


# ============================================================
# 4. 加载目标字段
# ============================================================

def _load_target_column(
    *,
    db: Session,
    column_id: int,
) -> tuple[
    DataColumn,
    DataTable,
]:
    """
    查询目标字段和所属数据表。
    """

    row = db.execute(
        select(
            DataColumn,
            DataTable,
        )
        .join(
            DataTable,
            DataTable.id
            == DataColumn.table_id,
        )
        .where(
            DataColumn.id == column_id
        )
    ).one_or_none()

    if row is None:
        raise ValueError(
            f"字段不存在：column_id={column_id}"
        )

    target_column, target_table = row

    return (
        target_column,
        target_table,
    )


# ============================================================
# 5. 加载已确认来源
# ============================================================

def _load_confirmed_sources(
    *,
    db: Session,
    target_column_id: int,
) -> tuple[
    LineageExplanationSource,
    ...,
]:
    """
    只加载 resolution_status=confirmed 的血缘。

    ambiguous 和 unresolved 不用于生成确定性解释。
    """

    source_column = aliased(
        DataColumn
    )

    source_table = aliased(
        DataTable
    )

    rows = db.execute(
        select(
            ColumnLineage,
            source_column,
            source_table,
        )
        .outerjoin(
            source_column,
            source_column.id
            == ColumnLineage.source_column_id,
        )
        .outerjoin(
            source_table,
            source_table.id
            == source_column.table_id,
        )
        .where(
            ColumnLineage.target_column_id
            == target_column_id,
            ColumnLineage.resolution_status
            == "confirmed",
        )
        .order_by(
            ColumnLineage.statement_no,
            ColumnLineage.id,
        )
    ).all()

    result: list[
        LineageExplanationSource
    ] = []

    for (
        lineage,
        current_source_column,
        current_source_table,
    ) in rows:
        if (
            current_source_column is not None
            and current_source_table is not None
        ):
            source_full_name = (
                f"{current_source_table.full_name}."
                f"{current_source_column.column_name}"
            )

            source_column_id = (
                current_source_column.id
            )

            source_table_full_name = (
                current_source_table.full_name
            )

            source_column_name = (
                current_source_column.column_name
            )

        else:
            # 为以后支持常量字段预留。
            source_full_name = None
            source_column_id = None
            source_table_full_name = None
            source_column_name = None

        result.append(
            LineageExplanationSource(
                lineage_id=lineage.id,
                source_column_id=(
                    source_column_id
                ),
                source_table_full_name=(
                    source_table_full_name
                ),
                source_column_name=(
                    source_column_name
                ),
                source_full_name=(
                    source_full_name
                ),
                relation_type=(
                    lineage.relation_type
                ),
                expression_text=(
                    lineage.expression_text
                ),
            )
        )

    return tuple(result)


# ============================================================
# 6. 加载并去重代码证据
# ============================================================

def _load_evidences(
    *,
    db: Session,
    lineage_ids: tuple[int, ...],
) -> tuple[
    LineageExplanationEvidence,
    ...,
]:
    """
    查询血缘证据，并按代码位置和代码内容去重。

    一个表达式可能产生多条字段血缘，例如：

        price * quantity AS amount

    会生成：

        price    -> amount
        quantity -> amount

    两条血缘可能绑定相同代码证据。
    面向用户展示时只需要展示一次源码。
    """

    if not lineage_ids:
        return ()

    rows = db.execute(
        select(
            LineageEvidence,
            SourceScript,
        )
        .join(
            SourceScript,
            SourceScript.id
            == LineageEvidence.script_id,
        )
        .where(
            LineageEvidence.column_lineage_id.in_(
                lineage_ids
            )
        )
        .order_by(
            SourceScript.relative_path,
            LineageEvidence.statement_no,
            LineageEvidence.evidence_order,
            LineageEvidence.id,
        )
    ).all()

    result: list[
        LineageExplanationEvidence
    ] = []

    seen_keys: set[
        tuple[
            int,
            int,
            int | None,
            int | None,
            str,
            str | None,
        ]
    ] = set()

    for evidence, script in rows:
        deduplicate_key = (
            script.id,
            evidence.statement_no,
            evidence.line_start,
            evidence.line_end,
            evidence.code_snippet,
            evidence.expression_text,
        )

        if deduplicate_key in seen_keys:
            continue

        seen_keys.add(
            deduplicate_key
        )

        result.append(
            LineageExplanationEvidence(
                evidence_id=evidence.id,
                script_id=script.id,
                file_name=script.file_name,
                relative_path=(
                    script.relative_path
                ),
                statement_no=(
                    evidence.statement_no
                ),
                line_start=evidence.line_start,
                line_end=evidence.line_end,
                code_snippet=(
                    evidence.code_snippet
                ),
                expression_text=(
                    evidence.expression_text
                ),
            )
        )

    return tuple(result)


# ============================================================
# 7. 按关系和表达式分组
# ============================================================

def _group_sources(
    sources: tuple[
        LineageExplanationSource,
        ...,
    ],
) -> tuple[
    tuple[
        str,
        str | None,
        tuple[str, ...],
    ],
    ...,
]:
    """
    将使用同一个表达式生成目标字段的来源合并。

    返回结构：

        (
            (
                relation_type,
                expression_text,
                (
                    source_full_name,
                    ...
                ),
            ),
            ...
        )
    """

    groups: dict[
        tuple[str, str | None],
        list[str],
    ] = {}

    for source in sources:
        key = (
            source.relation_type,
            source.expression_text,
        )

        group_sources = groups.setdefault(
            key,
            [],
        )

        if (
            source.source_full_name is not None
            and source.source_full_name
            not in group_sources
        ):
            group_sources.append(
                source.source_full_name
            )

    return tuple(
        (
            relation_type,
            expression_text,
            tuple(source_names),
        )
        for (
            relation_type,
            expression_text,
        ), source_names in groups.items()
    )


# ============================================================
# 8. 生成自然语言说明
# ============================================================

def _build_natural_language(
    *,
    target_full_name: str,
    sources: tuple[
        LineageExplanationSource,
        ...,
    ],
) -> str:
    """
    使用确定性规则生成自然语言。

    不调用LLM，不补充数据库中不存在的信息。
    """

    if not sources:
        return (
            f"字段 {target_full_name} "
            "当前没有已确认的字段血缘。"
        )

    groups = _group_sources(
        sources
    )

    sentences: list[str] = []

    for (
        relation_type,
        expression_text,
        source_names,
    ) in groups:
        source_text = "、".join(
            source_names
        )

        if not source_text:
            source_text = "常量或无物理来源表达式"

        if (
            relation_type == "direct"
            and len(source_names) == 1
        ):
            sentence = (
                f"字段 {target_full_name} "
                f"直接来源于 {source_text}。"
            )

        elif relation_type == "aggregate":
            sentence = (
                f"字段 {target_full_name} "
                f"由 {source_text} 通过聚合表达式"
                f"「{expression_text or '未记录'}」"
                "计算得到。"
            )

        elif relation_type == "constant":
            sentence = (
                f"字段 {target_full_name} "
                f"由常量表达式"
                f"「{expression_text or '未记录'}」"
                "生成。"
            )

        else:
            sentence = (
                f"字段 {target_full_name} "
                f"由 {source_text} 通过表达式"
                f"「{expression_text or '未记录'}」"
                "计算得到。"
            )

        sentences.append(
            sentence
        )

    return "".join(sentences)


# ============================================================
# 9. 生成伪代码
# ============================================================

def _build_pseudocode(
    *,
    target_full_name: str,
    sources: tuple[
        LineageExplanationSource,
        ...,
    ],
) -> str:
    """
    根据已确认血缘生成伪代码。
    """

    if not sources:
        return (
            f"{target_full_name} := UNKNOWN"
        )

    groups = _group_sources(
        sources
    )

    rules: list[str] = []

    multiple_groups = len(groups) > 1

    for rule_number, (
        relation_type,
        expression_text,
        source_names,
    ) in enumerate(
        groups,
        start=1,
    ):
        lines: list[str] = []

        if multiple_groups:
            lines.append(
                f"RULE {rule_number}:"
            )

        if (
            relation_type == "direct"
            and len(source_names) == 1
        ):
            lines.append(
                f"{target_full_name} := "
                f"{source_names[0]}"
            )

        else:
            lines.append(
                f"{target_full_name} := "
                f"{expression_text or 'UNKNOWN'}"
            )

            if source_names:
                lines.append(
                    "SOURCES:"
                )

                for source_name in source_names:
                    lines.append(
                        f"  - {source_name}"
                    )

        rules.append(
            "\n".join(lines)
        )

    return "\n\n".join(
        rules
    )


# ============================================================
# 10. 公共查询入口
# ============================================================

def explain_column_lineage(
    *,
    db: Session,
    column_id: int,
) -> ColumnLineageExplanation:
    """
    根据已确认字段血缘生成解释。

    严格原则：

        1. 只读取数据库中已经保存的血缘；
        2. 只使用 confirmed；
        3. 不通过字段名猜测来源；
        4. 不调用LLM；
        5. 不生成数据库中不存在的业务含义。
    """

    if column_id <= 0:
        raise ValueError(
            "column_id 必须大于 0"
        )

    (
        target_column,
        target_table,
    ) = _load_target_column(
        db=db,
        column_id=column_id,
    )

    target_full_name = (
        f"{target_table.full_name}."
        f"{target_column.column_name}"
    )

    sources = _load_confirmed_sources(
        db=db,
        target_column_id=(
            target_column.id
        ),
    )

    lineage_ids = tuple(
        source.lineage_id
        for source in sources
    )

    evidences = _load_evidences(
        db=db,
        lineage_ids=lineage_ids,
    )

    natural_language = (
        _build_natural_language(
            target_full_name=(
                target_full_name
            ),
            sources=sources,
        )
    )

    pseudocode = _build_pseudocode(
        target_full_name=target_full_name,
        sources=sources,
    )

    return ColumnLineageExplanation(
        target_column_id=target_column.id,
        target_table_full_name=(
            target_table.full_name
        ),
        target_column_name=(
            target_column.column_name
        ),
        target_full_name=target_full_name,
        has_confirmed_lineage=bool(
            sources
        ),
        natural_language=(
            natural_language
        ),
        pseudocode=pseudocode,
        sources=sources,
        evidences=evidences,
    )
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
    SourceScript,
)
from app.services.column_lineage_extractor import (
    DirectColumnLineageResult,
)


# ============================================================
# 1. 持久化结果
# ============================================================

@dataclass(frozen=True)
class ColumnLineagePersistenceResult:
    """
    一次字段血缘持久化的统计结果。

    source_table_id:
        为了兼容以前的单来源表代码而保留。

        单来源表：
            返回该来源表ID。

        多来源表：
            返回None。

    source_table_ids:
        当前SQL所有来源表ID。
    """

    project_id: int
    script_id: int
    statement_no: int

    source_table_id: int | None

    source_table_ids: tuple[
        int,
        ...
    ]

    target_table_id: int

    created_column_count: int
    reused_column_count: int

    deleted_lineage_count: int

    created_lineage_count: int
    created_evidence_count: int

    lineage_ids: tuple[int, ...]


# ============================================================
# 2. 检查项目
# ============================================================

def _get_project_or_raise(
    db: Session,
    project_id: int,
) -> LineageProject:
    """
    查询血缘项目。

    项目不存在时不允许继续保存字段血缘。
    """

    project = db.get(
        LineageProject,
        project_id,
    )

    if project is None:
        raise ValueError(
            f"血缘项目不存在：project_id={project_id}"
        )

    return project


# ============================================================
# 3. 检查脚本
# ============================================================

def _get_script_or_raise(
    db: Session,
    project_id: int,
    script_id: int,
) -> SourceScript:
    """
    查询脚本，并验证脚本属于指定项目。
    """

    script = db.get(
        SourceScript,
        script_id,
    )

    if script is None:
        raise ValueError(
            f"源码脚本不存在：script_id={script_id}"
        )

    if script.project_id != project_id:
        raise ValueError(
            "源码脚本不属于指定项目："
            f"project_id={project_id}, "
            f"script_id={script_id}"
        )

    return script


# ============================================================
# 4. 查询数据表
# ============================================================

def _get_table_or_raise(
    db: Session,
    project_id: int,
    full_name: str,
    table_role: str,
) -> DataTable:
    """
    根据项目ID和完整表名查询数据表。

    table_role用于生成明确错误，例如：

        来源表不存在
        目标表不存在
    """

    table = db.scalar(
        select(DataTable)
        .where(
            DataTable.project_id
            == project_id,
            DataTable.full_name
            == full_name,
        )
    )

    if table is None:
        raise ValueError(
            f"{table_role}不存在："
            f"project_id={project_id}, "
            f"full_name={full_name}"
        )

    return table


# ============================================================
# 5. 创建或复用字段
# ============================================================

def _get_or_create_column(
    db: Session,
    table: DataTable,
    column_name: str,
    ordinal_position: int | None,
) -> tuple[
    DataColumn,
    bool,
]:
    """
    根据：

        table_id
        column_name

    创建或者复用字段。

    返回：

        (
            DataColumn,
            created,
        )

    created=True:
        本次创建了字段。

    created=False:
        字段已经存在，本次直接复用。
    """

    column = db.scalar(
        select(DataColumn)
        .where(
            DataColumn.table_id
            == table.id,
            DataColumn.column_name
            == column_name,
        )
    )

    if column is not None:
        # 原来没有字段位置，
        # 这次能够取得目标字段位置时补充。
        if (
            column.ordinal_position is None
            and ordinal_position is not None
        ):
            column.ordinal_position = (
                ordinal_position
            )

        return column, False

    column = DataColumn(
        table_id=table.id,
        column_name=column_name,
        ordinal_position=ordinal_position,
        data_type=None,
    )

    db.add(column)
    db.flush()

    return column, True


# ============================================================
# 6. 删除当前语句原来的字段血缘
# ============================================================

def _delete_existing_lineages(
    db: Session,
    project_id: int,
    script_id: int,
    statement_no: int,
) -> int:
    """
    删除当前脚本、当前语句以前生成的字段血缘。

    重新导入SQL时采用：

        删除旧血缘
        → 保存新血缘

    避免重复产生：

        第一次2条
        第二次4条
        第三次6条

    LineageEvidence会通过ORM级联关系删除。
    """

    existing_lineages = list(
        db.scalars(
            select(ColumnLineage)
            .where(
                ColumnLineage.project_id
                == project_id,
                ColumnLineage.script_id
                == script_id,
                ColumnLineage.statement_no
                == statement_no,
            )
        ).all()
    )

    for lineage in existing_lineages:
        db.delete(lineage)

    if existing_lineages:
        db.flush()

    return len(existing_lineages)


# ============================================================
# 7. 提取Mapping中实际使用的来源表名
# ============================================================

def _get_mapping_source_table_names(
    extraction_result: (
        DirectColumnLineageResult
    ),
) -> tuple[str, ...]:
    """
    从Mapping中取得实际使用的来源表。

    不直接只依赖：

        extraction_result.source_table_full_names

    是因为SQL的JOIN表可能只出现在ON条件中，
    但没有字段进入SELECT投影。

    字段血缘持久化只需要处理真正参与目标字段生成的来源表。
    """

    source_table_names: list[str] = []

    for mapping in (
        extraction_result.mappings
    ):
        source_table_name = (
            mapping.source_table_full_name
        )

        if (
            source_table_name
            not in source_table_names
        ):
            source_table_names.append(
                source_table_name
            )

    return tuple(source_table_names)


# ============================================================
# 8. 正式持久化字段血缘
# ============================================================

def persist_direct_column_lineage(
    db: Session,
    project_id: int,
    script_id: int,
    statement_no: int,
    extraction_result: (
        DirectColumnLineageResult
    ),
) -> ColumnLineagePersistenceResult:
    """
    把字段血缘提取结果写入数据库。

    支持：

        单来源表
        多来源JOIN
        direct
        transform
        aggregate

    注意：

    1. 本函数会执行flush；
    2. 本函数不会执行commit；
    3. 最终事务由上层调用者控制。
    """

    if statement_no < 1:
        raise ValueError(
            "statement_no 必须大于或等于 1"
        )

    if not extraction_result.mappings:
        raise ValueError(
            "字段血缘提取结果不能为空"
        )

    # --------------------------------------------------------
    # 第一步：验证项目和脚本
    # --------------------------------------------------------

    _get_project_or_raise(
        db=db,
        project_id=project_id,
    )

    _get_script_or_raise(
        db=db,
        project_id=project_id,
        script_id=script_id,
    )

    # --------------------------------------------------------
    # 第二步：查询目标表
    # --------------------------------------------------------

    target_table = _get_table_or_raise(
        db=db,
        project_id=project_id,
        full_name=(
            extraction_result
            .target_table_full_name
        ),
        table_role="目标表",
    )

    # --------------------------------------------------------
    # 第三步：查询所有实际来源表
    # --------------------------------------------------------

    source_table_names = (
        _get_mapping_source_table_names(
            extraction_result
        )
    )

    if not source_table_names:
        raise ValueError(
            "字段血缘没有可用的来源表"
        )

    source_tables_by_name: dict[
        str,
        DataTable,
    ] = {}

    for source_table_name in (
        source_table_names
    ):
        source_table = (
            _get_table_or_raise(
                db=db,
                project_id=project_id,
                full_name=source_table_name,
                table_role="来源表",
            )
        )

        source_tables_by_name[
            source_table_name
        ] = source_table

    # 按提取结果中的来源表顺序生成ID。
    source_table_ids = tuple(
        source_tables_by_name[
            table_name
        ].id
        for table_name in source_table_names
    )

    # 单来源表继续返回原来的source_table_id。
    #
    # 多来源表时返回None，
    # 防止调用者误以为只有一个来源表。
    source_table_id = (
        source_table_ids[0]
        if len(source_table_ids) == 1
        else None
    )

    # --------------------------------------------------------
    # 第四步：删除当前语句以前的血缘
    # --------------------------------------------------------

    deleted_lineage_count = (
        _delete_existing_lineages(
            db=db,
            project_id=project_id,
            script_id=script_id,
            statement_no=statement_no,
        )
    )

    # --------------------------------------------------------
    # 第五步：创建字段、血缘和证据
    # --------------------------------------------------------

    created_column_count = 0
    reused_column_count = 0

    created_lineage_count = 0
    created_evidence_count = 0

    lineage_ids: list[int] = []

    for mapping in (
        extraction_result.mappings
    ):
        # ----------------------------------------------------
        # 检查Mapping目标表是否一致
        # ----------------------------------------------------

        if (
            mapping.target_table_full_name
            != target_table.full_name
        ):
            raise ValueError(
                "Mapping目标表与提取结果目标表不一致："
                f"mapping={mapping.target_table_full_name}, "
                f"result={target_table.full_name}"
            )

        # ----------------------------------------------------
        # 根据每条Mapping选择真实来源表
        # ----------------------------------------------------

        source_table = (
            source_tables_by_name.get(
                mapping
                .source_table_full_name
            )
        )

        if source_table is None:
            raise ValueError(
                "Mapping引用了未加载的来源表："
                f"{mapping.source_table_full_name}"
            )

        # ----------------------------------------------------
        # 创建或复用来源字段
        # ----------------------------------------------------
        #
        # SQL只能可靠给出来源字段名称，
        # 不能确定来源物理表中的真实字段顺序，
        # 所以ordinal_position暂时为None。
        # ----------------------------------------------------

        (
            source_column,
            source_column_created,
        ) = _get_or_create_column(
            db=db,
            table=source_table,
            column_name=(
                mapping.source_column_name
            ),
            ordinal_position=None,
        )

        if source_column_created:
            created_column_count += 1
        else:
            reused_column_count += 1

        # ----------------------------------------------------
        # 创建或复用目标字段
        # ----------------------------------------------------

        (
            target_column,
            target_column_created,
        ) = _get_or_create_column(
            db=db,
            table=target_table,
            column_name=(
                mapping.target_column_name
            ),
            ordinal_position=(
                mapping.ordinal_position
            ),
        )

        if target_column_created:
            created_column_count += 1
        else:
            reused_column_count += 1

        # ----------------------------------------------------
        # 创建字段血缘
        # ----------------------------------------------------

        lineage = ColumnLineage(
            project_id=project_id,
            script_id=script_id,
            target_column_id=(
                target_column.id
            ),
            source_column_id=(
                source_column.id
            ),
            relation_type=(
                mapping.relation_type
            ),
            resolution_status="confirmed",
            expression_text=(
                mapping.expression_text
            ),
            statement_no=statement_no,
        )

        db.add(lineage)
        db.flush()

        lineage_ids.append(
            lineage.id
        )

        created_lineage_count += 1

        # ----------------------------------------------------
        # 创建血缘代码证据
        # ----------------------------------------------------

        evidence = LineageEvidence(
            column_lineage_id=(
                lineage.id
            ),
            script_id=script_id,
            statement_no=statement_no,
            evidence_order=1,
            line_start=None,
            line_end=None,
            code_snippet=(
                mapping.expression_text
            ),
            expression_text=(
                mapping.expression_text
            ),
        )

        db.add(evidence)

        created_evidence_count += 1

    db.flush()

    # --------------------------------------------------------
    # 第六步：返回持久化统计
    # --------------------------------------------------------

    return ColumnLineagePersistenceResult(
        project_id=project_id,
        script_id=script_id,
        statement_no=statement_no,
        source_table_id=(
            source_table_id
        ),
        source_table_ids=(
            source_table_ids
        ),
        target_table_id=(
            target_table.id
        ),
        created_column_count=(
            created_column_count
        ),
        reused_column_count=(
            reused_column_count
        ),
        deleted_lineage_count=(
            deleted_lineage_count
        ),
        created_lineage_count=(
            created_lineage_count
        ),
        created_evidence_count=(
            created_evidence_count
        ),
        lineage_ids=tuple(
            lineage_ids
        ),
    )
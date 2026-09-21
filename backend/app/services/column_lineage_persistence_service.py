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
    """

    project_id: int
    script_id: int
    statement_no: int

    source_table_id: int
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

    项目不存在时，不允许继续写字段血缘。
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

    不能只根据 script_id 查询以后直接使用，
    因为必须防止把一个项目的字段血缘写入另一个项目。
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
    根据项目和完整表名查询数据表。

    table_role 用于生成更明确的错误信息，例如：

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

    查找字段。

    返回：

        (column, created)

    created=True:
        本次创建了新字段。

    created=False:
        数据库中已经存在该字段，本次直接复用。
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
        # 如果原来的字段没有位置，
        # 但这次解析取得了字段位置，就补充位置。
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
# 6. 删除当前语句以前的字段血缘
# ============================================================

def _delete_existing_lineages(
    db: Session,
    project_id: int,
    script_id: int,
    statement_no: int,
) -> int:
    """
    删除当前脚本、当前语句以前生成的字段血缘。

    这样重复解析同一个脚本时，不会出现：

        第一次生成 3 条
        第二次又增加 3 条
        第三次又增加 3 条

    正确行为应该是：

        删除旧结果
        重新保存新结果

    LineageEvidence 和 ColumnLineage 配置了级联关系，
    删除 ColumnLineage 时，其证据也会一起删除。
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
# 7. 正式持久化函数
# ============================================================

def persist_direct_column_lineage(
    db: Session,
    project_id: int,
    script_id: int,
    statement_no: int,
    extraction_result: DirectColumnLineageResult,
) -> ColumnLineagePersistenceResult:
    """
    把直接字段血缘提取结果写入数据库。

    注意：

    1. 本函数会执行 flush；
    2. 本函数不会执行 commit；
    3. 事务是否提交由上层调用者决定。

    数据写入顺序：

        检查项目
            ↓
        检查脚本
            ↓
        查找来源表
            ↓
        查找目标表
            ↓
        删除同一语句的旧字段血缘
            ↓
        创建或复用 DataColumn
            ↓
        创建 ColumnLineage
            ↓
        创建 LineageEvidence
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
    # 第二步：查询来源表和目标表
    # --------------------------------------------------------

    source_table = _get_table_or_raise(
        db=db,
        project_id=project_id,
        full_name=(
            extraction_result
            .source_table_full_name
        ),
        table_role="来源表",
    )

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
    # 第三步：删除当前语句原来的血缘
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
    # 第四步：创建字段、血缘和证据
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
        # 创建或复用来源字段
        # ----------------------------------------------------
        #
        # 当前只能从 SQL 中知道来源字段名称，
        # 不能可靠地知道来源表中字段的实际顺序，
        # 因此来源字段 ordinal_position 暂时使用 None。
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
        #
        # 目标字段顺序来自：
        #
        # INSERT INTO target (
        #     第1个字段,
        #     第2个字段
        # )
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
            target_column_id=target_column.id,
            source_column_id=source_column.id,
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

        created_lineage_count += 1

        lineage_ids.append(
            lineage.id
        )

        # ----------------------------------------------------
        # 创建代码证据
        # ----------------------------------------------------
        #
        # 当前阶段还没有精确计算源码行号，
        # 所以 line_start 和 line_end 暂时为 None。
        #
        # code_snippet 保存 SELECT 投影表达式，例如：
        #
        #     order_id
        #
        # 或：
        #
        #     source_order_id AS order_id
        # ----------------------------------------------------

        evidence = LineageEvidence(
            column_lineage_id=lineage.id,
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
    # 第五步：返回持久化统计
    # --------------------------------------------------------

    return ColumnLineagePersistenceResult(
        project_id=project_id,
        script_id=script_id,
        statement_no=statement_no,
        source_table_id=source_table.id,
        target_table_id=target_table.id,
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
        lineage_ids=tuple(lineage_ids),
    )
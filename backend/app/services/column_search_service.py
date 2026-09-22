from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    case,
    func,
    or_,
    select,
)
from sqlalchemy.orm import (
    Session,
    selectinload,
)

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageProject,
)


# ============================================================
# 1. 字段搜索结果项
# ============================================================

@dataclass(frozen=True)
class ColumnSearchItem:
    """
    字段搜索结果中的一条记录。
    """

    project_id: int

    table_id: int
    column_id: int

    catalog_name: str | None
    schema_name: str | None

    table_name: str
    table_full_name: str
    table_kind: str

    column_name: str
    ordinal_position: int | None
    data_type: str | None

    # 有多少条字段血缘指向当前字段
    upstream_lineage_count: int

    # 当前字段指向多少条下游字段血缘
    downstream_lineage_count: int


# ============================================================
# 2. 字段分页搜索结果
# ============================================================

@dataclass(frozen=True)
class ColumnSearchResult:
    """
    一个项目内的字段分页搜索结果。
    """

    project_id: int
    project_name: str

    page: int
    page_size: int
    total: int

    items: tuple[
        ColumnSearchItem,
        ...
    ]


# ============================================================
# 3. 分页参数检查
# ============================================================

def _validate_pagination(
    page: int,
    page_size: int,
) -> None:
    """
    page 从1开始。

    page_size 最大100，
    防止一次查询过多字段。
    """

    if page < 1:
        raise ValueError(
            "page 必须大于或等于 1"
        )

    if page_size < 1:
        raise ValueError(
            "page_size 必须大于或等于 1"
        )

    if page_size > 100:
        raise ValueError(
            "page_size 不能大于 100"
        )


# ============================================================
# 4. 清理搜索参数
# ============================================================

def _normalize_optional_text(
    value: str | None,
) -> str | None:
    """
    将：

        None
        ""
        "   "

    统一转换成 None。
    """

    if value is None:
        return None

    normalized_value = value.strip()

    if not normalized_value:
        return None

    return normalized_value


# ============================================================
# 5. 查询项目
# ============================================================

def _get_project_or_raise(
    db: Session,
    project_id: int,
) -> LineageProject:
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
# 6. 批量查询字段上游数量
# ============================================================

def _get_upstream_lineage_counts(
    db: Session,
    column_ids: list[int],
) -> dict[int, int]:
    """
    当前字段作为 target_column 时，
    统计有多少条上游血缘。
    """

    if not column_ids:
        return {}

    rows = db.execute(
        select(
            ColumnLineage.target_column_id,
            func.count(
                ColumnLineage.id
            ),
        )
        .where(
            ColumnLineage
            .target_column_id
            .in_(column_ids)
        )
        .group_by(
            ColumnLineage.target_column_id
        )
    ).all()

    return {
        column_id: int(lineage_count)
        for column_id, lineage_count in rows
    }


# ============================================================
# 7. 批量查询字段下游数量
# ============================================================

def _get_downstream_lineage_counts(
    db: Session,
    column_ids: list[int],
) -> dict[int, int]:
    """
    当前字段作为 source_column 时，
    统计有多少条下游血缘。
    """

    if not column_ids:
        return {}

    rows = db.execute(
        select(
            ColumnLineage.source_column_id,
            func.count(
                ColumnLineage.id
            ),
        )
        .where(
            ColumnLineage
            .source_column_id
            .in_(column_ids)
        )
        .group_by(
            ColumnLineage.source_column_id
        )
    ).all()

    return {
        column_id: int(lineage_count)
        for column_id, lineage_count in rows
    }


# ============================================================
# 8. 正式搜索项目字段
# ============================================================

def search_project_columns(
    db: Session,
    project_id: int,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    table_name: str | None = None,
    column_name: str | None = None,
) -> ColumnSearchResult:
    """
    搜索指定项目中的字段。

    keyword:
        同时模糊搜索：

        catalog_name
        schema_name
        table_name
        full_name
        column_name

    table_name:
        模糊搜索：

        table_name
        full_name

    column_name:
        模糊搜索字段名称。

    多个参数同时提供时使用 AND 关系。

    例如：

        table_name=orders
        column_name=amount

    表示搜索 orders 表中的 amount 字段。
    """

    _validate_pagination(
        page=page,
        page_size=page_size,
    )

    project = _get_project_or_raise(
        db=db,
        project_id=project_id,
    )

    normalized_keyword = (
        _normalize_optional_text(
            keyword
        )
    )

    normalized_table_name = (
        _normalize_optional_text(
            table_name
        )
    )

    normalized_column_name = (
        _normalize_optional_text(
            column_name
        )
    )

    filters = [
        DataTable.project_id
        == project_id
    ]

    # --------------------------------------------------------
    # 全局关键字
    # --------------------------------------------------------

    if normalized_keyword is not None:
        keyword_pattern = (
            f"%{normalized_keyword}%"
        )

        filters.append(
            or_(
                DataTable.catalog_name.like(
                    keyword_pattern
                ),
                DataTable.schema_name.like(
                    keyword_pattern
                ),
                DataTable.table_name.like(
                    keyword_pattern
                ),
                DataTable.full_name.like(
                    keyword_pattern
                ),
                DataColumn.column_name.like(
                    keyword_pattern
                ),
            )
        )

    # --------------------------------------------------------
    # 表名筛选
    # --------------------------------------------------------

    if normalized_table_name is not None:
        table_pattern = (
            f"%{normalized_table_name}%"
        )

        filters.append(
            or_(
                DataTable.table_name.like(
                    table_pattern
                ),
                DataTable.full_name.like(
                    table_pattern
                ),
            )
        )

    # --------------------------------------------------------
    # 字段名筛选
    # --------------------------------------------------------

    if normalized_column_name is not None:
        column_pattern = (
            f"%{normalized_column_name}%"
        )

        filters.append(
            DataColumn.column_name.like(
                column_pattern
            )
        )

    # --------------------------------------------------------
    # 查询总数
    # --------------------------------------------------------

    total_statement = (
        select(
            func.count(
                DataColumn.id
            )
        )
        .select_from(DataColumn)
        .join(
            DataTable,
            DataTable.id
            == DataColumn.table_id,
        )
        .where(*filters)
    )

    total = int(
        db.scalar(total_statement) or 0
    )

    # --------------------------------------------------------
    # 查询当前页
    # --------------------------------------------------------

    offset = (
        page - 1
    ) * page_size

    # MySQL 不统一支持：
    #
    #     NULLS LAST
    #
    # 因此使用 CASE：
    #
    # ordinal_position 非空 -> 0
    # ordinal_position 为空 -> 1
    #
    # 让有明确位置的字段排在前面。
    null_position_order = case(
        (
            DataColumn
            .ordinal_position
            .is_(None),
            1,
        ),
        else_=0,
    )

    columns = list(
        db.scalars(
            select(DataColumn)
            .join(
                DataTable,
                DataTable.id
                == DataColumn.table_id,
            )
            .options(
                selectinload(
                    DataColumn.table
                )
            )
            .where(*filters)
            .order_by(
                DataTable.full_name.asc(),
                null_position_order.asc(),
                DataColumn
                .ordinal_position
                .asc(),
                DataColumn.column_name.asc(),
                DataColumn.id.asc(),
            )
            .offset(offset)
            .limit(page_size)
        ).all()
    )

    # --------------------------------------------------------
    # 批量查询上下游统计
    # --------------------------------------------------------

    column_ids = [
        column.id
        for column in columns
    ]

    upstream_counts = (
        _get_upstream_lineage_counts(
            db=db,
            column_ids=column_ids,
        )
    )

    downstream_counts = (
        _get_downstream_lineage_counts(
            db=db,
            column_ids=column_ids,
        )
    )

    # --------------------------------------------------------
    # 组装返回结果
    # --------------------------------------------------------

    items: list[
        ColumnSearchItem
    ] = []

    for column in columns:
        table = column.table

        items.append(
            ColumnSearchItem(
                project_id=project.id,
                table_id=table.id,
                column_id=column.id,
                catalog_name=(
                    table.catalog_name
                ),
                schema_name=(
                    table.schema_name
                ),
                table_name=(
                    table.table_name
                ),
                table_full_name=(
                    table.full_name
                ),
                table_kind=(
                    table.table_kind
                ),
                column_name=(
                    column.column_name
                ),
                ordinal_position=(
                    column.ordinal_position
                ),
                data_type=(
                    column.data_type
                ),
                upstream_lineage_count=(
                    upstream_counts.get(
                        column.id,
                        0,
                    )
                ),
                downstream_lineage_count=(
                    downstream_counts.get(
                        column.id,
                        0,
                    )
                ),
            )
        )

    return ColumnSearchResult(
        project_id=project.id,
        project_name=project.name,
        page=page,
        page_size=page_size,
        total=total,
        items=tuple(items),
    )
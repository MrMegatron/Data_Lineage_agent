from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import (
    Session,
    selectinload,
)

from app.models import (
    ColumnLineage,
    DataColumn,
    LineageEvidence,
)


# ============================================================
# 1. 字段节点
# ============================================================

@dataclass(frozen=True)
class ColumnNode:
    """
    字段血缘图中的一个字段节点。

    例如：

        dwd.orders.order_id
    """

    column_id: int
    table_id: int
    project_id: int

    catalog_name: str | None
    schema_name: str | None
    table_name: str
    table_full_name: str

    column_name: str
    ordinal_position: int | None
    data_type: str | None


# ============================================================
# 2. 血缘证据
# ============================================================

@dataclass(frozen=True)
class ColumnLineageEvidenceItem:
    """
    一条字段血缘对应的代码证据。
    """

    evidence_id: int

    statement_no: int
    evidence_order: int

    line_start: int | None
    line_end: int | None

    code_snippet: str
    expression_text: str | None

    created_at: datetime


# ============================================================
# 3. 字段血缘边
# ============================================================

@dataclass(frozen=True)
class ColumnLineageEdgeItem:
    """
    一条字段血缘边。

    例如：

        ods.orders.order_id
                 ↓
        dwd.orders.order_id

    source_column:
        来源字段。

    target_column:
        目标字段。

    script_id / script_path:
        哪个 SQL 脚本产生了这条血缘。
    """

    lineage_id: int

    source_column: ColumnNode | None
    target_column: ColumnNode

    script_id: int
    script_file_name: str
    script_path: str

    relation_type: str
    resolution_status: str

    expression_text: str | None
    statement_no: int

    evidences: tuple[
        ColumnLineageEvidenceItem,
        ...
    ]

    created_at: datetime


# ============================================================
# 4. 字段血缘查询结果
# ============================================================

@dataclass(frozen=True)
class ColumnLineageQueryResult:
    """
    一个字段的直接上下游查询结果。

    upstream_edges:
        以当前字段作为 target_column 的血缘。
        也就是哪些字段生成了当前字段。

    downstream_edges:
        以当前字段作为 source_column 的血缘。
        也就是当前字段生成了哪些字段。
    """

    project_id: int

    column: ColumnNode

    upstream_count: int
    downstream_count: int

    upstream_edges: tuple[
        ColumnLineageEdgeItem,
        ...
    ]

    downstream_edges: tuple[
        ColumnLineageEdgeItem,
        ...
    ]


# ============================================================
# 5. ORM字段转换成字段节点
# ============================================================

def _to_column_node(
    column: DataColumn,
) -> ColumnNode:
    """
    将 DataColumn ORM 对象转换成查询结果节点。
    """

    table = column.table

    return ColumnNode(
        column_id=column.id,
        table_id=table.id,
        project_id=table.project_id,
        catalog_name=table.catalog_name,
        schema_name=table.schema_name,
        table_name=table.table_name,
        table_full_name=table.full_name,
        column_name=column.column_name,
        ordinal_position=(
            column.ordinal_position
        ),
        data_type=column.data_type,
    )


# ============================================================
# 6. ORM证据转换成返回对象
# ============================================================

def _to_evidence_item(
    evidence: LineageEvidence,
) -> ColumnLineageEvidenceItem:
    return ColumnLineageEvidenceItem(
        evidence_id=evidence.id,
        statement_no=evidence.statement_no,
        evidence_order=evidence.evidence_order,
        line_start=evidence.line_start,
        line_end=evidence.line_end,
        code_snippet=evidence.code_snippet,
        expression_text=(
            evidence.expression_text
        ),
        created_at=evidence.created_at,
    )


# ============================================================
# 7. ORM血缘转换成血缘边
# ============================================================

def _to_edge_item(
    lineage: ColumnLineage,
) -> ColumnLineageEdgeItem:
    """
    将 ColumnLineage ORM 对象转换成字段血缘边。
    """

    source_column_node = None

    if lineage.source_column is not None:
        source_column_node = (
            _to_column_node(
                lineage.source_column
            )
        )

    target_column_node = (
        _to_column_node(
            lineage.target_column
        )
    )

    sorted_evidences = sorted(
        lineage.evidences,
        key=lambda evidence: (
            evidence.evidence_order,
            evidence.id,
        ),
    )

    evidence_items = tuple(
        _to_evidence_item(evidence)
        for evidence in sorted_evidences
    )

    return ColumnLineageEdgeItem(
        lineage_id=lineage.id,
        source_column=source_column_node,
        target_column=target_column_node,
        script_id=lineage.script.id,
        script_file_name=(
            lineage.script.file_name
        ),
        script_path=(
            lineage.script.relative_path
        ),
        relation_type=(
            lineage.relation_type
        ),
        resolution_status=(
            lineage.resolution_status
        ),
        expression_text=(
            lineage.expression_text
        ),
        statement_no=(
            lineage.statement_no
        ),
        evidences=evidence_items,
        created_at=lineage.created_at,
    )


# ============================================================
# 8. 统一的血缘加载配置
# ============================================================

def _lineage_load_options():
    """
    定义字段血缘查询需要预加载的关联对象。

    使用 selectinload 的目的：

    1. 避免循环中不断查询数据库；
    2. 避免典型的 N+1 查询；
    3. 确保转换结果时关联对象已经加载。
    """

    return (
        selectinload(
            ColumnLineage.source_column
        ).selectinload(
            DataColumn.table
        ),

        selectinload(
            ColumnLineage.target_column
        ).selectinload(
            DataColumn.table
        ),

        selectinload(
            ColumnLineage.script
        ),

        selectinload(
            ColumnLineage.evidences
        ),
    )


# ============================================================
# 9. 查询字段直接上下游
# ============================================================

def get_column_lineage_detail(
    db: Session,
    column_id: int,
) -> ColumnLineageQueryResult:
    """
    根据 column_id 查询字段详情及直接上下游。

    例如当前字段：

        dwd.orders.order_id

    可能得到：

        直接上游：
            ods.orders.order_id

        直接下游：
            ads.order_report.order_id
    """

    # --------------------------------------------------------
    # 第一步：查询当前字段及所属数据表
    # --------------------------------------------------------

    column = db.scalar(
        select(DataColumn)
        .options(
            selectinload(
                DataColumn.table
            )
        )
        .where(
            DataColumn.id == column_id
        )
    )

    if column is None:
        raise ValueError(
            f"数据字段不存在：column_id={column_id}"
        )

    current_column_node = (
        _to_column_node(column)
    )

    # --------------------------------------------------------
    # 第二步：查询直接上游
    #
    # 当前字段作为 target_column。
    # --------------------------------------------------------

    upstream_lineages = list(
        db.scalars(
            select(ColumnLineage)
            .options(
                *_lineage_load_options()
            )
            .where(
                ColumnLineage.target_column_id
                == column_id
            )
            .order_by(
                ColumnLineage.statement_no,
                ColumnLineage.id,
            )
        ).all()
    )

    # --------------------------------------------------------
    # 第三步：查询直接下游
    #
    # 当前字段作为 source_column。
    # --------------------------------------------------------

    downstream_lineages = list(
        db.scalars(
            select(ColumnLineage)
            .options(
                *_lineage_load_options()
            )
            .where(
                ColumnLineage.source_column_id
                == column_id
            )
            .order_by(
                ColumnLineage.statement_no,
                ColumnLineage.id,
            )
        ).all()
    )

    # --------------------------------------------------------
    # 第四步：转换成返回对象
    # --------------------------------------------------------

    upstream_edges = tuple(
        _to_edge_item(lineage)
        for lineage in upstream_lineages
    )

    downstream_edges = tuple(
        _to_edge_item(lineage)
        for lineage in downstream_lineages
    )

    return ColumnLineageQueryResult(
        project_id=(
            current_column_node.project_id
        ),
        column=current_column_node,
        upstream_count=len(
            upstream_edges
        ),
        downstream_count=len(
            downstream_edges
        ),
        upstream_edges=upstream_edges,
        downstream_edges=downstream_edges,
    )

# ============================================================
# 10. 多层血缘图中的边
# ============================================================

@dataclass(frozen=True)
class ColumnLineageGraphEdge:
    """
    多层字段血缘图中的一条边。

    depth:
        这条边距离根字段的层数。

    traversal_direction:
        查询这条边时使用的遍历方向：

        upstream
        downstream
    """

    depth: int
    traversal_direction: str

    lineage: ColumnLineageEdgeItem


# ============================================================
# 11. 多层字段血缘图结果
# ============================================================

@dataclass(frozen=True)
class ColumnLineageGraphResult:
    """
    字段多层血缘图。

    direction:

        upstream
            只查询多层上游。

        downstream
            只查询多层下游。

        both
            同时查询上游和下游。
    """

    project_id: int

    root_column: ColumnNode

    direction: str
    max_depth: int

    node_count: int
    edge_count: int

    nodes: tuple[
        ColumnNode,
        ...
    ]

    edges: tuple[
        ColumnLineageGraphEdge,
        ...
    ]


# ============================================================
# 12. 查询根字段
# ============================================================

def _get_column_node_or_raise(
    db: Session,
    column_id: int,
) -> tuple[
    DataColumn,
    ColumnNode,
]:
    """
    查询字段并转换为字段节点。
    """

    column = db.scalar(
        select(DataColumn)
        .options(
            selectinload(
                DataColumn.table
            )
        )
        .where(
            DataColumn.id == column_id
        )
    )

    if column is None:
        raise ValueError(
            f"数据字段不存在：column_id={column_id}"
        )

    return (
        column,
        _to_column_node(column),
    )


# ============================================================
# 13. 批量查询某一层的上游血缘
# ============================================================

def _load_upstream_lineages(
    db: Session,
    target_column_ids: set[int],
) -> list[ColumnLineage]:
    """
    查询：

        source_column
              ↓
        target_column

    target_column_id 在当前层字段集合中的血缘。
    """

    if not target_column_ids:
        return []

    return list(
        db.scalars(
            select(ColumnLineage)
            .options(
                *_lineage_load_options()
            )
            .where(
                ColumnLineage
                .target_column_id
                .in_(
                    target_column_ids
                )
            )
            .order_by(
                ColumnLineage.id
            )
        ).all()
    )


# ============================================================
# 14. 批量查询某一层的下游血缘
# ============================================================

def _load_downstream_lineages(
    db: Session,
    source_column_ids: set[int],
) -> list[ColumnLineage]:
    """
    查询：

        source_column
              ↓
        target_column

    source_column_id 在当前层字段集合中的血缘。
    """

    if not source_column_ids:
        return []

    return list(
        db.scalars(
            select(ColumnLineage)
            .options(
                *_lineage_load_options()
            )
            .where(
                ColumnLineage
                .source_column_id
                .in_(
                    source_column_ids
                )
            )
            .order_by(
                ColumnLineage.id
            )
        ).all()
    )


# ============================================================
# 15. 执行单方向广度优先遍历
# ============================================================

def _traverse_column_lineage(
    db: Session,
    root_column_id: int,
    direction: str,
    max_depth: int,
    nodes_by_id: dict[
        int,
        ColumnNode,
    ],
    edges_by_id: dict[
        int,
        ColumnLineageGraphEdge,
    ],
) -> None:
    """
    使用广度优先搜索遍历字段血缘。

    visited_column_ids 用于防止：

        A -> B
        B -> C
        C -> A

    这种循环血缘导致无限查询。
    """

    frontier_column_ids = {
        root_column_id
    }

    visited_column_ids = {
        root_column_id
    }

    for depth in range(
        1,
        max_depth + 1,
    ):
        if not frontier_column_ids:
            break

        if direction == "upstream":
            lineages = (
                _load_upstream_lineages(
                    db=db,
                    target_column_ids=(
                        frontier_column_ids
                    ),
                )
            )

        else:
            lineages = (
                _load_downstream_lineages(
                    db=db,
                    source_column_ids=(
                        frontier_column_ids
                    ),
                )
            )

        next_frontier: set[int] = set()

        for lineage in lineages:
            edge_item = _to_edge_item(
                lineage
            )

            # ------------------------------------------------
            # 保存来源节点
            # ------------------------------------------------

            if (
                edge_item.source_column
                is not None
            ):
                source_node = (
                    edge_item.source_column
                )

                nodes_by_id[
                    source_node.column_id
                ] = source_node

            # ------------------------------------------------
            # 保存目标节点
            # ------------------------------------------------

            target_node = (
                edge_item.target_column
            )

            nodes_by_id[
                target_node.column_id
            ] = target_node

            # ------------------------------------------------
            # 保存血缘边
            #
            # 同一条 lineage_id 只保存一次。
            # ------------------------------------------------

            if (
                lineage.id
                not in edges_by_id
            ):
                edges_by_id[
                    lineage.id
                ] = (
                    ColumnLineageGraphEdge(
                        depth=depth,
                        traversal_direction=(
                            direction
                        ),
                        lineage=edge_item,
                    )
                )

            # ------------------------------------------------
            # 计算下一层字段
            # ------------------------------------------------

            if direction == "upstream":
                if (
                    edge_item.source_column
                    is None
                ):
                    continue

                next_column_id = (
                    edge_item
                    .source_column
                    .column_id
                )

            else:
                next_column_id = (
                    edge_item
                    .target_column
                    .column_id
                )

            if (
                next_column_id
                not in visited_column_ids
            ):
                visited_column_ids.add(
                    next_column_id
                )

                next_frontier.add(
                    next_column_id
                )

        frontier_column_ids = (
            next_frontier
        )


# ============================================================
# 16. 正式查询多层字段血缘
# ============================================================

def get_column_lineage_graph(
    db: Session,
    column_id: int,
    direction: str = "both",
    max_depth: int = 10,
) -> ColumnLineageGraphResult:
    """
    查询字段的多层血缘图。

    direction:

        upstream
        downstream
        both

    max_depth:

        最小 1
        最大 20

    为什么限制最大深度：

    1. 防止错误数据导致查询范围过大；
    2. 防止一个请求遍历整个企业数据仓库；
    3. 让 API 查询时间保持可控。
    """

    normalized_direction = (
        direction.strip().lower()
    )

    allowed_directions = {
        "upstream",
        "downstream",
        "both",
    }

    if (
        normalized_direction
        not in allowed_directions
    ):
        raise ValueError(
            "direction 必须是 "
            "upstream、downstream 或 both"
        )

    if max_depth < 1:
        raise ValueError(
            "max_depth 必须大于或等于 1"
        )

    if max_depth > 20:
        raise ValueError(
            "max_depth 不能大于 20"
        )

    (
        root_column,
        root_column_node,
    ) = _get_column_node_or_raise(
        db=db,
        column_id=column_id,
    )

    nodes_by_id: dict[
        int,
        ColumnNode,
    ] = {
        root_column.id:
            root_column_node
    }

    edges_by_id: dict[
        int,
        ColumnLineageGraphEdge,
    ] = {}

    # --------------------------------------------------------
    # 查询多层上游
    # --------------------------------------------------------

    if normalized_direction in {
        "upstream",
        "both",
    }:
        _traverse_column_lineage(
            db=db,
            root_column_id=root_column.id,
            direction="upstream",
            max_depth=max_depth,
            nodes_by_id=nodes_by_id,
            edges_by_id=edges_by_id,
        )

    # --------------------------------------------------------
    # 查询多层下游
    # --------------------------------------------------------

    if normalized_direction in {
        "downstream",
        "both",
    }:
        _traverse_column_lineage(
            db=db,
            root_column_id=root_column.id,
            direction="downstream",
            max_depth=max_depth,
            nodes_by_id=nodes_by_id,
            edges_by_id=edges_by_id,
        )

    # --------------------------------------------------------
    # 统一排序
    #
    # 保证相同数据库数据每次返回顺序稳定，
    # 方便前端展示和自动化测试。
    # --------------------------------------------------------

    sorted_nodes = tuple(
        sorted(
            nodes_by_id.values(),
            key=lambda node: (
                node.table_full_name,
                node.ordinal_position
                if (
                    node.ordinal_position
                    is not None
                )
                else 999999,
                node.column_name,
                node.column_id,
            ),
        )
    )

    sorted_edges = tuple(
        sorted(
            edges_by_id.values(),
            key=lambda edge: (
                edge.depth,
                edge.lineage.lineage_id,
            ),
        )
    )

    return ColumnLineageGraphResult(
        project_id=(
            root_column_node.project_id
        ),
        root_column=root_column_node,
        direction=normalized_direction,
        max_depth=max_depth,
        node_count=len(sorted_nodes),
        edge_count=len(sorted_edges),
        nodes=sorted_nodes,
        edges=sorted_edges,
    )
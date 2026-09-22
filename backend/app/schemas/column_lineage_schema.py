from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


# ============================================================
# 1. 字段节点
# ============================================================

class ColumnNodeResponse(BaseModel):
    """
    字段血缘图中的一个字段节点。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

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

class ColumnLineageEvidenceResponse(
    BaseModel
):
    """
    字段血缘对应的源码证据。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

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

class ColumnLineageEdgeResponse(
    BaseModel
):
    """
    一条来源字段到目标字段的血缘关系。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

    lineage_id: int

    source_column: (
        ColumnNodeResponse
        | None
    )

    target_column: ColumnNodeResponse

    script_id: int
    script_file_name: str
    script_path: str

    relation_type: str
    resolution_status: str

    expression_text: str | None
    statement_no: int

    evidences: list[
        ColumnLineageEvidenceResponse
    ] = Field(
        default_factory=list
    )

    created_at: datetime


# ============================================================
# 4. 字段血缘完整响应
# ============================================================

class ColumnLineageResponse(BaseModel):
    """
    查询一个字段的直接上下游结果。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int

    column: ColumnNodeResponse

    upstream_count: int
    downstream_count: int

    upstream_edges: list[
        ColumnLineageEdgeResponse
    ] = Field(
        default_factory=list
    )

    downstream_edges: list[
        ColumnLineageEdgeResponse
    ] = Field(
        default_factory=list
    )

# ============================================================
# 5. 多层字段血缘边
# ============================================================

class ColumnLineageGraphEdgeResponse(
    BaseModel
):
    """
    多层字段血缘图中的一条边。

    depth:
        当前血缘边距离根字段的层数。

    traversal_direction:
        这条边是通过 upstream 还是 downstream
        遍历得到的。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

    depth: int
    traversal_direction: str

    lineage: ColumnLineageEdgeResponse


# ============================================================
# 6. 多层字段血缘图
# ============================================================

class ColumnLineageGraphResponse(
    BaseModel
):
    """
    一个字段的多层上下游血缘图。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int

    root_column: ColumnNodeResponse

    direction: str
    max_depth: int

    node_count: int
    edge_count: int

    nodes: list[
        ColumnNodeResponse
    ] = Field(
        default_factory=list
    )

    edges: list[
        ColumnLineageGraphEdgeResponse
    ] = Field(
        default_factory=list
    )
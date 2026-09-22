from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


# ============================================================
# 1. 字段搜索结果项
# ============================================================

class ColumnSearchItemResponse(
    BaseModel
):
    """
    字段搜索结果中的一条字段记录。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

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

    upstream_lineage_count: int
    downstream_lineage_count: int


# ============================================================
# 2. 字段分页搜索响应
# ============================================================

class ColumnSearchResponse(BaseModel):
    """
    一个项目内的字段分页搜索响应。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int
    project_name: str

    page: int
    page_size: int
    total: int

    items: list[
        ColumnSearchItemResponse
    ] = Field(
        default_factory=list
    )
from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
)


# ============================================================
# 1. 项目列表项
# ============================================================

class ProjectListItemResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int
    name: str
    description: str | None

    script_count: int
    table_count: int
    dependency_count: int

    created_at: datetime
    updated_at: datetime


# ============================================================
# 2. 项目分页响应
# ============================================================

class ProjectListResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    page: int
    page_size: int
    total: int

    items: list[
        ProjectListItemResponse
    ]

# ============================================================
# 3. 脚本列表项
# ============================================================

class ScriptListItemResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    script_id: int

    file_name: str
    relative_path: str

    dialect: str
    parse_status: str
    parse_error: str | None

    read_table_count: int
    write_table_count: int

    upstream_dependency_count: int
    downstream_dependency_count: int

    created_at: datetime
    updated_at: datetime


# ============================================================
# 4. 项目脚本分页响应
# ============================================================

class ScriptListResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int
    project_name: str

    page: int
    page_size: int
    total: int

    items: list[
        ScriptListItemResponse
    ]


# ============================================================
# 5. 数据表列表项
# ============================================================

class TableListItemResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    table_id: int

    catalog_name: str | None
    schema_name: str | None
    table_name: str
    full_name: str
    table_kind: str

    column_count: int

    writer_script_count: int
    reader_script_count: int

    dependency_count: int

    created_at: datetime


# ============================================================
# 6. 项目数据表分页响应
# ============================================================

class TableListResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int
    project_name: str

    page: int
    page_size: int
    total: int

    items: list[
        TableListItemResponse
    ]
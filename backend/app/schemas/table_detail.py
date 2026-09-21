from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
)


# ============================================================
# 1. 字段响应
# ============================================================

class TableColumnDetailResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    column_id: int
    column_name: str
    ordinal_position: int | None
    data_type: str | None
    created_at: datetime


# ============================================================
# 2. 脚本访问响应
# ============================================================

class TableScriptAccessResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    access_id: int

    script_id: int
    file_name: str
    relative_path: str

    access_type: Literal[
        "read",
        "write",
    ]

    statement_no: int

    line_start: int | None
    line_end: int | None

    evidence_sql: str | None


# ============================================================
# 3. 脚本依赖响应
# ============================================================

class TableScriptDependencyResponse(
    BaseModel
):
    model_config = ConfigDict(
        from_attributes=True
    )

    dependency_id: int

    upstream_script_id: int
    upstream_file_name: str
    upstream_relative_path: str

    downstream_script_id: int
    downstream_file_name: str
    downstream_relative_path: str

    dependency_status: Literal[
        "confirmed",
        "ambiguous",
    ]

    reason: str | None


# ============================================================
# 4. 数据表详情响应
# ============================================================

class TableDetailResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int
    project_name: str

    table_id: int

    catalog_name: str | None
    schema_name: str | None
    table_name: str
    full_name: str

    table_kind: Literal[
        "physical",
        "view",
        "temp",
        "unknown",
    ]

    created_at: datetime

    column_count: int
    columns: list[
        TableColumnDetailResponse
    ]

    access_count: int
    writer_script_count: int
    reader_script_count: int

    writers: list[
        TableScriptAccessResponse
    ]

    readers: list[
        TableScriptAccessResponse
    ]

    dependency_count: int
    confirmed_dependency_count: int
    ambiguous_dependency_count: int

    dependencies: list[
        TableScriptDependencyResponse
    ]
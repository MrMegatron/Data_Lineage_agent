from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
)


class ScriptTableAccessDetailResponse(
    BaseModel
):
    model_config = ConfigDict(
        from_attributes=True
    )

    access_id: int
    access_type: Literal[
        "read",
        "write",
    ]

    statement_no: int

    table_id: int
    catalog_name: str | None
    schema_name: str | None
    table_name: str
    full_name: str

    line_start: int | None
    line_end: int | None

    evidence_sql: str | None


class RelatedScriptDependencyResponse(
    BaseModel
):
    model_config = ConfigDict(
        from_attributes=True
    )

    dependency_id: int

    related_script_id: int
    related_file_name: str
    related_relative_path: str

    via_table_id: int
    via_table_full_name: str

    dependency_status: Literal[
        "confirmed",
        "ambiguous",
    ]

    reason: str | None


class ScriptDetailResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int
    project_name: str

    script_id: int
    file_name: str
    relative_path: str

    dialect: Literal[
        "hive",
        "spark",
        "postgresql",
        "unknown",
    ]

    file_hash: str
    source_code: str

    parse_status: Literal[
        "pending",
        "success",
        "failed",
    ]

    parse_error: str | None

    created_at: datetime
    updated_at: datetime

    table_access_count: int
    read_table_count: int
    write_table_count: int

    upstream_count: int
    downstream_count: int

    table_accesses: list[
        ScriptTableAccessDetailResponse
    ]

    upstream_dependencies: list[
        RelatedScriptDependencyResponse
    ]

    downstream_dependencies: list[
        RelatedScriptDependencyResponse
    ]
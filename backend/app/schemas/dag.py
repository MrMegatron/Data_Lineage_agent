from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
)


# ============================================================
# 1. DAG 节点响应
# ============================================================

class ScriptDagNodeResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    script_id: int
    file_name: str
    relative_path: str

    dialect: Literal[
        "hive",
        "spark",
        "postgresql",
        "unknown",
    ]

    parse_status: Literal[
        "pending",
        "success",
        "failed",
    ]

    in_degree: int
    out_degree: int


# ============================================================
# 2. DAG 边响应
# ============================================================

class ScriptDagEdgeResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    dependency_id: int

    upstream_script_id: int
    downstream_script_id: int

    via_table_id: int
    via_table_full_name: str

    dependency_status: Literal[
        "confirmed",
        "ambiguous",
    ]

    reason: str | None


# ============================================================
# 3. 项目 DAG 响应
# ============================================================

class ProjectScriptDagResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    project_id: int
    project_name: str

    node_count: int
    edge_count: int

    confirmed_edge_count: int
    ambiguous_edge_count: int

    nodes: list[
        ScriptDagNodeResponse
    ]

    edges: list[
        ScriptDagEdgeResponse
    ]
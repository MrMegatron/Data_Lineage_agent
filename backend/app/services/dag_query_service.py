from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    SourceScript,
)


# ============================================================
# 1. DAG 脚本节点
# ============================================================

@dataclass(frozen=True, slots=True)
class ScriptDagNode:
    """
    DAG 中的一个脚本节点。
    """

    script_id: int
    file_name: str
    relative_path: str
    dialect: str
    parse_status: str

    # 有多少条边进入这个节点
    in_degree: int

    # 有多少条边从这个节点出去
    out_degree: int


# ============================================================
# 2. DAG 依赖边
# ============================================================

@dataclass(frozen=True, slots=True)
class ScriptDagEdge:
    """
    DAG 中的一条脚本依赖边。
    """

    dependency_id: int

    upstream_script_id: int
    downstream_script_id: int

    via_table_id: int
    via_table_full_name: str

    dependency_status: str
    reason: str | None


# ============================================================
# 3. 整个项目的 DAG
# ============================================================

@dataclass(frozen=True, slots=True)
class ProjectScriptDag:
    """
    一个项目的完整脚本级 DAG。
    """

    project_id: int
    project_name: str

    node_count: int
    edge_count: int

    confirmed_edge_count: int
    ambiguous_edge_count: int

    nodes: tuple[
        ScriptDagNode,
        ...
    ]

    edges: tuple[
        ScriptDagEdge,
        ...
    ]


# ============================================================
# 4. 查询项目脚本 DAG
# ============================================================

def get_project_script_dag(
    db: Session,
    project_id: int,
) -> ProjectScriptDag:
    """
    查询一个项目的完整脚本级 DAG。

    节点来源：

        SourceScript

    边来源：

        ScriptDependency

    即使一个脚本没有任何上游或下游，
    也会作为独立节点返回。
    """

    # --------------------------------------------------------
    # 1. 检查项目是否存在
    # --------------------------------------------------------

    project = db.get(
        LineageProject,
        project_id,
    )

    if project is None:
        raise ValueError(
            f"血缘项目不存在：project_id={project_id}"
        )

    # --------------------------------------------------------
    # 2. 查询项目全部脚本
    # --------------------------------------------------------

    scripts = db.scalars(
        select(SourceScript)
        .where(
            SourceScript.project_id
            == project_id
        )
        .order_by(
            SourceScript.relative_path,
            SourceScript.id,
        )
    ).all()

    script_by_id = {
        script.id: script
        for script in scripts
    }

    # --------------------------------------------------------
    # 3. 查询项目全部依赖
    # --------------------------------------------------------

    dependencies = db.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project_id
        )
    ).all()

    # --------------------------------------------------------
    # 4. 加载依赖经过的表名
    # --------------------------------------------------------

    via_table_ids = {
        dependency.via_table_id
        for dependency in dependencies
    }

    if via_table_ids:
        data_tables = db.scalars(
            select(DataTable).where(
                DataTable.id.in_(
                    via_table_ids
                )
            )
        ).all()

    else:
        data_tables = []

    table_name_by_id = {
        data_table.id:
        data_table.full_name
        for data_table in data_tables
    }

    # --------------------------------------------------------
    # 5. 过滤项目范围异常的数据
    # --------------------------------------------------------

    valid_dependencies = [
        dependency
        for dependency in dependencies
        if (
            dependency.upstream_script_id
            in script_by_id
            and
            dependency.downstream_script_id
            in script_by_id
        )
    ]

    # --------------------------------------------------------
    # 6. 对边进行稳定排序
    # --------------------------------------------------------

    valid_dependencies.sort(
        key=lambda dependency: (
            script_by_id[
                dependency.upstream_script_id
            ].relative_path.casefold(),

            script_by_id[
                dependency.downstream_script_id
            ].relative_path.casefold(),

            table_name_by_id.get(
                dependency.via_table_id,
                "",
            ).casefold(),

            dependency.id,
        )
    )

    # --------------------------------------------------------
    # 7. 计算节点入度和出度
    # --------------------------------------------------------

    in_degree_by_script = {
        script.id: 0
        for script in scripts
    }

    out_degree_by_script = {
        script.id: 0
        for script in scripts
    }

    for dependency in valid_dependencies:

        out_degree_by_script[
            dependency.upstream_script_id
        ] += 1

        in_degree_by_script[
            dependency.downstream_script_id
        ] += 1

    # --------------------------------------------------------
    # 8. 构建节点
    # --------------------------------------------------------

    nodes = tuple(
        ScriptDagNode(
            script_id=script.id,
            file_name=script.file_name,
            relative_path=(
                script.relative_path
            ),
            dialect=script.dialect,
            parse_status=(
                script.parse_status
            ),
            in_degree=(
                in_degree_by_script[
                    script.id
                ]
            ),
            out_degree=(
                out_degree_by_script[
                    script.id
                ]
            ),
        )
        for script in scripts
    )

    # --------------------------------------------------------
    # 9. 构建边
    # --------------------------------------------------------

    edges = tuple(
        ScriptDagEdge(
            dependency_id=dependency.id,

            upstream_script_id=(
                dependency.upstream_script_id
            ),

            downstream_script_id=(
                dependency.downstream_script_id
            ),

            via_table_id=(
                dependency.via_table_id
            ),

            via_table_full_name=(
                table_name_by_id.get(
                    dependency.via_table_id,
                    f"table_id={dependency.via_table_id}",
                )
            ),

            dependency_status=(
                dependency.dependency_status
            ),

            reason=dependency.reason,
        )
        for dependency in valid_dependencies
    )

    confirmed_edge_count = sum(
        edge.dependency_status
        == "confirmed"
        for edge in edges
    )

    ambiguous_edge_count = sum(
        edge.dependency_status
        == "ambiguous"
        for edge in edges
    )

    return ProjectScriptDag(
        project_id=project.id,
        project_name=project.name,
        node_count=len(nodes),
        edge_count=len(edges),
        confirmed_edge_count=(
            confirmed_edge_count
        ),
        ambiguous_edge_count=(
            ambiguous_edge_count
        ),
        nodes=nodes,
        edges=edges,
    )
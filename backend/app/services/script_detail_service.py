from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)


# ============================================================
# 1. 脚本表访问详情
# ============================================================

@dataclass(frozen=True, slots=True)
class ScriptTableAccessDetail:
    access_id: int

    access_type: str
    statement_no: int

    table_id: int
    catalog_name: str | None
    schema_name: str | None
    table_name: str
    full_name: str

    line_start: int | None
    line_end: int | None

    evidence_sql: str | None


# ============================================================
# 2. 相关脚本依赖详情
# ============================================================

@dataclass(frozen=True, slots=True)
class RelatedScriptDependencyDetail:
    dependency_id: int

    related_script_id: int
    related_file_name: str
    related_relative_path: str

    via_table_id: int
    via_table_full_name: str

    dependency_status: str
    reason: str | None


# ============================================================
# 3. 脚本完整详情
# ============================================================

@dataclass(frozen=True, slots=True)
class ScriptDetailResult:
    project_id: int
    project_name: str

    script_id: int
    file_name: str
    relative_path: str

    dialect: str
    file_hash: str
    source_code: str

    parse_status: str
    parse_error: str | None

    created_at: datetime
    updated_at: datetime

    table_access_count: int
    read_table_count: int
    write_table_count: int

    upstream_count: int
    downstream_count: int

    table_accesses: tuple[
        ScriptTableAccessDetail,
        ...
    ]

    upstream_dependencies: tuple[
        RelatedScriptDependencyDetail,
        ...
    ]

    downstream_dependencies: tuple[
        RelatedScriptDependencyDetail,
        ...
    ]


# ============================================================
# 4. 查询脚本详情
# ============================================================

def get_script_detail(
    db: Session,
    project_id: int,
    script_id: int,
) -> ScriptDetailResult:
    """
    查询一个脚本的完整详情。

    安全规则：

    脚本必须属于指定项目，否则返回不存在。
    """

    # --------------------------------------------------------
    # 1. 检查项目
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
    # 2. 查询指定项目中的脚本
    # --------------------------------------------------------

    script = db.scalar(
        select(SourceScript).where(
            SourceScript.id == script_id,
            SourceScript.project_id
            == project_id,
        )
    )

    if script is None:
        raise ValueError(
            "脚本不存在或不属于当前项目："
            f"script_id={script_id}"
        )

    # --------------------------------------------------------
    # 3. 查询 READ / WRITE
    # --------------------------------------------------------

    access_rows = db.execute(
        select(
            ScriptTableAccess,
            DataTable,
        )
        .join(
            DataTable,
            DataTable.id
            == ScriptTableAccess.table_id,
        )
        .where(
            ScriptTableAccess.script_id
            == script_id
        )
    ).all()

    table_accesses = [
        ScriptTableAccessDetail(
            access_id=access.id,
            access_type=access.access_type,
            statement_no=access.statement_no,
            table_id=data_table.id,
            catalog_name=(
                data_table.catalog_name
            ),
            schema_name=(
                data_table.schema_name
            ),
            table_name=(
                data_table.table_name
            ),
            full_name=data_table.full_name,
            line_start=access.line_start,
            line_end=access.line_end,
            evidence_sql=(
                access.evidence_sql
            ),
        )
        for access, data_table
        in access_rows
    ]

    table_accesses.sort(
        key=lambda access: (
            access.statement_no,
            access.access_type,
            access.full_name.casefold(),
            access.access_id,
        )
    )

    # --------------------------------------------------------
    # 4. 查询上游和下游依赖
    # --------------------------------------------------------

    dependencies = db.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project_id,

            or_(
                ScriptDependency.upstream_script_id
                == script_id,

                ScriptDependency.downstream_script_id
                == script_id,
            ),
        )
    ).all()

    related_script_ids: set[int] = set()
    via_table_ids: set[int] = set()

    for dependency in dependencies:
        related_script_ids.add(
            dependency.upstream_script_id
        )

        related_script_ids.add(
            dependency.downstream_script_id
        )

        via_table_ids.add(
            dependency.via_table_id
        )

    related_script_ids.discard(
        script_id
    )

    # --------------------------------------------------------
    # 5. 加载相关脚本
    # --------------------------------------------------------

    if related_script_ids:
        related_scripts = db.scalars(
            select(SourceScript).where(
                SourceScript.id.in_(
                    related_script_ids
                )
            )
        ).all()

    else:
        related_scripts = []

    related_script_by_id = {
        related_script.id:
        related_script
        for related_script
        in related_scripts
    }

    # --------------------------------------------------------
    # 6. 加载依赖经过的数据表
    # --------------------------------------------------------

    if via_table_ids:
        via_tables = db.scalars(
            select(DataTable).where(
                DataTable.id.in_(
                    via_table_ids
                )
            )
        ).all()

    else:
        via_tables = []

    via_table_by_id = {
        via_table.id: via_table
        for via_table in via_tables
    }

    upstream_dependencies: list[
        RelatedScriptDependencyDetail
    ] = []

    downstream_dependencies: list[
        RelatedScriptDependencyDetail
    ] = []

    # --------------------------------------------------------
    # 7. 区分上游与下游
    # --------------------------------------------------------

    for dependency in dependencies:

        via_table = via_table_by_id.get(
            dependency.via_table_id
        )

        via_table_full_name = (
            via_table.full_name
            if via_table is not None
            else (
                "table_id="
                f"{dependency.via_table_id}"
            )
        )

        # 当前脚本是下游：
        #
        # related script -> current script
        if (
            dependency.downstream_script_id
            == script_id
        ):
            related_script = (
                related_script_by_id.get(
                    dependency.upstream_script_id
                )
            )

            if related_script is not None:
                upstream_dependencies.append(
                    RelatedScriptDependencyDetail(
                        dependency_id=(
                            dependency.id
                        ),
                        related_script_id=(
                            related_script.id
                        ),
                        related_file_name=(
                            related_script.file_name
                        ),
                        related_relative_path=(
                            related_script.relative_path
                        ),
                        via_table_id=(
                            dependency.via_table_id
                        ),
                        via_table_full_name=(
                            via_table_full_name
                        ),
                        dependency_status=(
                            dependency.dependency_status
                        ),
                        reason=dependency.reason,
                    )
                )

        # 当前脚本是上游：
        #
        # current script -> related script
        if (
            dependency.upstream_script_id
            == script_id
        ):
            related_script = (
                related_script_by_id.get(
                    dependency.downstream_script_id
                )
            )

            if related_script is not None:
                downstream_dependencies.append(
                    RelatedScriptDependencyDetail(
                        dependency_id=(
                            dependency.id
                        ),
                        related_script_id=(
                            related_script.id
                        ),
                        related_file_name=(
                            related_script.file_name
                        ),
                        related_relative_path=(
                            related_script.relative_path
                        ),
                        via_table_id=(
                            dependency.via_table_id
                        ),
                        via_table_full_name=(
                            via_table_full_name
                        ),
                        dependency_status=(
                            dependency.dependency_status
                        ),
                        reason=dependency.reason,
                    )
                )

    # --------------------------------------------------------
    # 8. 稳定排序
    # --------------------------------------------------------

    upstream_dependencies.sort(
        key=lambda dependency: (
            dependency.related_relative_path
            .casefold(),

            dependency.via_table_full_name
            .casefold(),

            dependency.dependency_id,
        )
    )

    downstream_dependencies.sort(
        key=lambda dependency: (
            dependency.related_relative_path
            .casefold(),

            dependency.via_table_full_name
            .casefold(),

            dependency.dependency_id,
        )
    )

    read_table_count = sum(
        access.access_type == "read"
        for access in table_accesses
    )

    write_table_count = sum(
        access.access_type == "write"
        for access in table_accesses
    )

    return ScriptDetailResult(
        project_id=project.id,
        project_name=project.name,

        script_id=script.id,
        file_name=script.file_name,
        relative_path=(
            script.relative_path
        ),

        dialect=script.dialect,
        file_hash=script.file_hash,
        source_code=script.source_code,

        parse_status=(
            script.parse_status
        ),
        parse_error=script.parse_error,

        created_at=script.created_at,
        updated_at=script.updated_at,

        table_access_count=len(
            table_accesses
        ),
        read_table_count=read_table_count,
        write_table_count=write_table_count,

        upstream_count=len(
            upstream_dependencies
        ),
        downstream_count=len(
            downstream_dependencies
        ),

        table_accesses=tuple(
            table_accesses
        ),
        upstream_dependencies=tuple(
            upstream_dependencies
        ),
        downstream_dependencies=tuple(
            downstream_dependencies
        ),
    )
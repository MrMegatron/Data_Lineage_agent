from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DataColumn,
    DataTable,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)


# ============================================================
# 1. 字段详情
# ============================================================

@dataclass(frozen=True, slots=True)
class TableColumnDetail:
    column_id: int
    column_name: str
    ordinal_position: int | None
    data_type: str | None
    created_at: datetime


# ============================================================
# 2. 脚本访问表的详情
# ============================================================

@dataclass(frozen=True, slots=True)
class TableScriptAccessDetail:
    access_id: int

    script_id: int
    file_name: str
    relative_path: str

    access_type: str
    statement_no: int

    line_start: int | None
    line_end: int | None
    evidence_sql: str | None


# ============================================================
# 3. 通过该表形成的脚本依赖
# ============================================================

@dataclass(frozen=True, slots=True)
class TableScriptDependencyDetail:
    dependency_id: int

    upstream_script_id: int
    upstream_file_name: str
    upstream_relative_path: str

    downstream_script_id: int
    downstream_file_name: str
    downstream_relative_path: str

    dependency_status: str
    reason: str | None


# ============================================================
# 4. 数据表完整详情
# ============================================================

@dataclass(frozen=True, slots=True)
class TableDetailResult:
    project_id: int
    project_name: str

    table_id: int

    catalog_name: str | None
    schema_name: str | None
    table_name: str
    full_name: str
    table_kind: str

    created_at: datetime

    column_count: int
    columns: tuple[
        TableColumnDetail,
        ...
    ]

    access_count: int
    writer_script_count: int
    reader_script_count: int

    writers: tuple[
        TableScriptAccessDetail,
        ...
    ]

    readers: tuple[
        TableScriptAccessDetail,
        ...
    ]

    dependency_count: int
    confirmed_dependency_count: int
    ambiguous_dependency_count: int

    dependencies: tuple[
        TableScriptDependencyDetail,
        ...
    ]


# ============================================================
# 5. 查询数据表详情
# ============================================================

def get_table_detail(
    db: Session,
    project_id: int,
    table_id: int,
) -> TableDetailResult:
    """
    查询一个项目中的数据表详情。

    安全规则：

    数据表必须属于指定项目。
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
    # 2. 查询项目中的数据表
    # --------------------------------------------------------

    data_table = db.scalar(
        select(DataTable).where(
            DataTable.id == table_id,
            DataTable.project_id
            == project_id,
        )
    )

    if data_table is None:
        raise ValueError(
            "数据表不存在或不属于当前项目："
            f"table_id={table_id}"
        )

    # --------------------------------------------------------
    # 3. 查询字段
    # --------------------------------------------------------

    data_columns = db.scalars(
        select(DataColumn).where(
            DataColumn.table_id
            == table_id
        )
    ).all()

    data_columns.sort(
        key=lambda column: (
            column.ordinal_position is None,
            (
                column.ordinal_position
                if column.ordinal_position
                is not None
                else 0
            ),
            column.column_name.casefold(),
            column.id,
        )
    )

    columns = tuple(
        TableColumnDetail(
            column_id=column.id,
            column_name=(
                column.column_name
            ),
            ordinal_position=(
                column.ordinal_position
            ),
            data_type=column.data_type,
            created_at=column.created_at,
        )
        for column in data_columns
    )

    # --------------------------------------------------------
    # 4. 查询哪些脚本访问了这张表
    # --------------------------------------------------------

    access_rows = db.execute(
        select(
            ScriptTableAccess,
            SourceScript,
        )
        .join(
            SourceScript,
            SourceScript.id
            == ScriptTableAccess.script_id,
        )
        .where(
            ScriptTableAccess.table_id
            == table_id,

            SourceScript.project_id
            == project_id,
        )
    ).all()

    access_details = [
        TableScriptAccessDetail(
            access_id=access.id,

            script_id=script.id,
            file_name=script.file_name,
            relative_path=(
                script.relative_path
            ),

            access_type=access.access_type,
            statement_no=(
                access.statement_no
            ),

            line_start=access.line_start,
            line_end=access.line_end,
            evidence_sql=(
                access.evidence_sql
            ),
        )
        for access, script in access_rows
    ]

    access_details.sort(
        key=lambda access: (
            access.access_type,
            access.relative_path.casefold(),
            access.statement_no,
            access.access_id,
        )
    )

    writers = tuple(
        access
        for access in access_details
        if access.access_type == "write"
    )

    readers = tuple(
        access
        for access in access_details
        if access.access_type == "read"
    )

    # 一个脚本可能在多条语句中多次访问同一张表，
    # 因此脚本数量需要按 script_id 去重。
    writer_script_ids = {
        access.script_id
        for access in writers
    }

    reader_script_ids = {
        access.script_id
        for access in readers
    }

    # --------------------------------------------------------
    # 5. 查询通过该表形成的脚本依赖
    # --------------------------------------------------------

    dependencies = db.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project_id,

            ScriptDependency.via_table_id
            == table_id,
        )
    ).all()

    related_script_ids: set[int] = set()

    for dependency in dependencies:
        related_script_ids.add(
            dependency.upstream_script_id
        )

        related_script_ids.add(
            dependency.downstream_script_id
        )

    if related_script_ids:
        related_scripts = db.scalars(
            select(SourceScript).where(
                SourceScript.id.in_(
                    related_script_ids
                ),
                SourceScript.project_id
                == project_id,
            )
        ).all()

    else:
        related_scripts = []

    script_by_id = {
        script.id: script
        for script in related_scripts
    }

    dependency_details: list[
        TableScriptDependencyDetail
    ] = []

    for dependency in dependencies:

        upstream_script = script_by_id.get(
            dependency.upstream_script_id
        )

        downstream_script = script_by_id.get(
            dependency.downstream_script_id
        )

        if (
            upstream_script is None
            or downstream_script is None
        ):
            continue

        dependency_details.append(
            TableScriptDependencyDetail(
                dependency_id=(
                    dependency.id
                ),

                upstream_script_id=(
                    upstream_script.id
                ),
                upstream_file_name=(
                    upstream_script.file_name
                ),
                upstream_relative_path=(
                    upstream_script.relative_path
                ),

                downstream_script_id=(
                    downstream_script.id
                ),
                downstream_file_name=(
                    downstream_script.file_name
                ),
                downstream_relative_path=(
                    downstream_script.relative_path
                ),

                dependency_status=(
                    dependency.dependency_status
                ),
                reason=dependency.reason,
            )
        )

    dependency_details.sort(
        key=lambda dependency: (
            dependency.upstream_relative_path
            .casefold(),

            dependency.downstream_relative_path
            .casefold(),

            dependency.dependency_id,
        )
    )

    dependency_details_tuple = tuple(
        dependency_details
    )

    confirmed_dependency_count = sum(
        dependency.dependency_status
        == "confirmed"
        for dependency
        in dependency_details_tuple
    )

    ambiguous_dependency_count = sum(
        dependency.dependency_status
        == "ambiguous"
        for dependency
        in dependency_details_tuple
    )

    return TableDetailResult(
        project_id=project.id,
        project_name=project.name,

        table_id=data_table.id,

        catalog_name=(
            data_table.catalog_name
        ),
        schema_name=(
            data_table.schema_name
        ),
        table_name=data_table.table_name,
        full_name=data_table.full_name,
        table_kind=data_table.table_kind,

        created_at=data_table.created_at,

        column_count=len(columns),
        columns=columns,

        access_count=len(
            access_details
        ),
        writer_script_count=len(
            writer_script_ids
        ),
        reader_script_count=len(
            reader_script_ids
        ),

        writers=writers,
        readers=readers,

        dependency_count=len(
            dependency_details_tuple
        ),
        confirmed_dependency_count=(
            confirmed_dependency_count
        ),
        ambiguous_dependency_count=(
            ambiguous_dependency_count
        ),

        dependencies=(
            dependency_details_tuple
        ),
    )
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)


# ============================================================
# 1. 脚本依赖生成结果
# ============================================================

@dataclass(frozen=True, slots=True)
class ScriptDependencyBuildResult:
    """
    一个项目的脚本依赖生成结果。
    """

    project_id: int

    # 重建前删除的旧依赖数量
    deleted_dependency_count: int

    # 本次新建的依赖总数
    total_dependency_count: int

    # confirmed 数量
    confirmed_dependency_count: int

    # ambiguous 数量
    ambiguous_dependency_count: int

    # 实际产生依赖关系的数据表数量
    via_table_count: int


# ============================================================
# 2. 删除项目旧依赖
# ============================================================

def _delete_old_dependencies(
    db: Session,
    project_id: int,
) -> int:
    """
    删除一个项目之前生成的全部 ScriptDependency。

    每次都根据最新 READ/WRITE 全量重建，
    避免保留已经失效的旧关系。
    """

    old_dependencies = db.scalars(
        select(ScriptDependency).where(
            ScriptDependency.project_id
            == project_id
        )
    ).all()

    deleted_count = len(
        old_dependencies
    )

    for dependency in old_dependencies:
        db.delete(dependency)

    # 立即执行 DELETE，避免后续 INSERT
    # 与唯一约束冲突。
    db.flush()

    return deleted_count


# ============================================================
# 3. 加载项目中的 READ / WRITE
# ============================================================

def _load_project_table_accesses(
    db: Session,
    project_id: int,
) -> list[tuple[int, int, str]]:
    """
    返回：

        [
            (
                script_id,
                table_id,
                access_type,
            ),
            ...
        ]

    通过 SourceScript.project_id 限制数据范围，
    避免不同项目之间错误建立依赖。
    """

    rows = db.execute(
        select(
            ScriptTableAccess.script_id,
            ScriptTableAccess.table_id,
            ScriptTableAccess.access_type,
        )
        .join(
            SourceScript,
            SourceScript.id
            == ScriptTableAccess.script_id,
        )
        .where(
            SourceScript.project_id
            == project_id
        )
    ).all()

    return [
        (
            int(row.script_id),
            int(row.table_id),
            str(row.access_type),
        )
        for row in rows
    ]


# ============================================================
# 4. 加载数据表名称
# ============================================================

def _load_table_names(
    db: Session,
    project_id: int,
) -> dict[int, str]:
    """
    返回：

        {
            table_id: full_name
        }
    """

    tables = db.scalars(
        select(DataTable).where(
            DataTable.project_id
            == project_id
        )
    ).all()

    return {
        data_table.id:
        data_table.full_name
        for data_table in tables
    }


# ============================================================
# 5. 重建项目脚本依赖
# ============================================================

def rebuild_script_dependencies(
    db: Session,
    project_id: int,
) -> ScriptDependencyBuildResult:
    """
    根据项目最新的 ScriptTableAccess，
    全量重建 ScriptDependency。

    推导规则：

        upstream WRITE table
                  ↓
                table
                  ↓
        downstream READ table

    本函数只执行 flush，不执行 commit。
    最终事务由 API 或上层服务负责。
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
    # 2. 删除旧依赖
    # --------------------------------------------------------

    deleted_dependency_count = (
        _delete_old_dependencies(
            db=db,
            project_id=project_id,
        )
    )

    # --------------------------------------------------------
    # 3. 加载最新 READ / WRITE
    # --------------------------------------------------------

    access_rows = (
        _load_project_table_accesses(
            db=db,
            project_id=project_id,
        )
    )

    # --------------------------------------------------------
    # 4. 按 table_id 分组
    # --------------------------------------------------------

    writers_by_table: dict[
        int,
        set[int],
    ] = defaultdict(set)

    readers_by_table: dict[
        int,
        set[int],
    ] = defaultdict(set)

    for (
        script_id,
        table_id,
        access_type,
    ) in access_rows:

        if access_type == "write":
            writers_by_table[
                table_id
            ].add(script_id)

        elif access_type == "read":
            readers_by_table[
                table_id
            ].add(script_id)

    table_names = _load_table_names(
        db=db,
        project_id=project_id,
    )

    total_dependency_count = 0
    confirmed_dependency_count = 0
    ambiguous_dependency_count = 0

    dependency_table_ids: set[int] = set()

    # --------------------------------------------------------
    # 5. 遍历存在读取者的表
    # --------------------------------------------------------

    for table_id in sorted(
        readers_by_table
    ):

        reader_script_ids = (
            readers_by_table[table_id]
        )

        writer_script_ids = (
            writers_by_table.get(
                table_id,
                set(),
            )
        )

        # 没有任何脚本写这张表，说明它可能是外部输入表。
        if not writer_script_ids:
            continue

        # ----------------------------------------------------
        # 6. 为每个下游读取脚本建立依赖
        # ----------------------------------------------------

        for downstream_script_id in sorted(
            reader_script_ids
        ):

            # 排除脚本自己，防止生成：
            #
            # A -> A
            eligible_writer_ids = sorted(
                writer_script_id
                for writer_script_id
                in writer_script_ids
                if (
                    writer_script_id
                    != downstream_script_id
                )
            )

            # 排除自己后没有其他写入脚本，
            # 不生成依赖。
            if not eligible_writer_ids:
                continue

            # 只有一个可能的上游，依赖可以确认。
            if len(eligible_writer_ids) == 1:
                dependency_status = (
                    "confirmed"
                )

                reason = None

            # 存在多个可能的上游，全部保留为 ambiguous。
            else:
                dependency_status = (
                    "ambiguous"
                )

                table_name = table_names.get(
                    table_id,
                    f"table_id={table_id}",
                )

                reason = (
                    f"表 {table_name} 存在 "
                    f"{len(eligible_writer_ids)} 个"
                    "可能的上游写入脚本"
                )

            # ------------------------------------------------
            # 7. 每个可能上游生成一条关系
            # ------------------------------------------------

            for upstream_script_id in (
                eligible_writer_ids
            ):

                dependency = ScriptDependency(
                    project_id=project_id,
                    upstream_script_id=(
                        upstream_script_id
                    ),
                    downstream_script_id=(
                        downstream_script_id
                    ),
                    via_table_id=table_id,
                    dependency_status=(
                        dependency_status
                    ),
                    reason=reason,
                )

                db.add(dependency)

                total_dependency_count += 1

                if (
                    dependency_status
                    == "confirmed"
                ):
                    confirmed_dependency_count += 1

                else:
                    ambiguous_dependency_count += 1

                dependency_table_ids.add(
                    table_id
                )

    db.flush()

    return ScriptDependencyBuildResult(
        project_id=project_id,
        deleted_dependency_count=(
            deleted_dependency_count
        ),
        total_dependency_count=(
            total_dependency_count
        ),
        confirmed_dependency_count=(
            confirmed_dependency_count
        ),
        ambiguous_dependency_count=(
            ambiguous_dependency_count
        ),
        via_table_count=len(
            dependency_table_ids
        ),
    )
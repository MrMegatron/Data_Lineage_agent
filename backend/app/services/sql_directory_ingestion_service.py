from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import LineageProject
from app.services.sql_file_scanner import (
    scan_sql_files,
)
from app.services.sql_ingestion_service import (
    SqlFileIngestionResult,
    ingest_scanned_sql_file,
)

from app.services.script_dependency_service import (
    ScriptDependencyBuildResult,
    rebuild_script_dependencies,
)

# ============================================================
# 1. 整个 SQL 目录的导入结果
# ============================================================

@dataclass(frozen=True, slots=True)
class SqlDirectoryIngestionResult:
    """
    一个 SQL 目录的批量导入结果。
    """

    # 扫描根目录的绝对路径
    root_directory: str

    # success、partial、failed 或 empty
    status: str

    # 扫描到的 SQL 文件总数
    total_files: int

    # 解析成功的文件数
    success_files: int

    # 解析失败的文件数
    failed_files: int

    # 新建的 SourceScript 数量
    created_scripts: int

    # 更新的 SourceScript 数量
    updated_scripts: int

    # 全部文件中的 SQL 语句总数
    total_statements: int

    # 成功解析的 SQL 语句数
    successful_statements: int

    # 解析失败的 SQL 语句数
    failed_statements: int

    # 保存的 READ/WRITE 记录总数
    table_access_count: int

    # 重建前删除的旧依赖数量
    deleted_dependency_count: int

    # 本次生成的依赖总数
    total_dependency_count: int

    # confirmed 依赖数量
    confirmed_dependency_count: int

    # ambiguous 依赖数量
    ambiguous_dependency_count: int

    # 实际产生依赖的数据表数量
    dependency_via_table_count: int

    # 每个 SQL 文件的导入结果
    files: tuple[
        SqlFileIngestionResult,
        ...
    ]


# ============================================================
# 2. 汇总整个目录的处理结果
# ============================================================

def _build_directory_result(
    root_directory: Path,
    file_results: list[
        SqlFileIngestionResult
    ],
    dependency_result: (
        ScriptDependencyBuildResult
    ),
) -> SqlDirectoryIngestionResult:
    """
    汇总所有 SQL 文件的导入结果。
    """

    total_files = len(file_results)

    success_files = sum(
        file_result.parse_status == "success"
        for file_result in file_results
    )

    failed_files = sum(
        file_result.parse_status == "failed"
        for file_result in file_results
    )

    created_scripts = sum(
        file_result.created_script
        for file_result in file_results
    )

    updated_scripts = sum(
        not file_result.created_script
        for file_result in file_results
    )

    total_statements = sum(
        file_result.statement_count
        for file_result in file_results
    )

    successful_statements = sum(
        file_result.success_statement_count
        for file_result in file_results
    )

    failed_statements = sum(
        file_result.failed_statement_count
        for file_result in file_results
    )

    table_access_count = sum(
        file_result.table_access_count
        for file_result in file_results
    )

    # --------------------------------------------------------
    # 确定整个目录的业务状态
    # --------------------------------------------------------

    if total_files == 0:
        status = "empty"

    elif failed_files == 0:
        status = "success"

    elif success_files == 0:
        status = "failed"

    else:
        status = "partial"

    return SqlDirectoryIngestionResult(
        root_directory=str(root_directory),
        status=status,
        total_files=total_files,
        success_files=success_files,
        failed_files=failed_files,
        created_scripts=created_scripts,
        updated_scripts=updated_scripts,
        total_statements=total_statements,
        successful_statements=(
            successful_statements
        ),
        failed_statements=failed_statements,
        table_access_count=table_access_count,

        deleted_dependency_count=(
            dependency_result
            .deleted_dependency_count
        ),

        total_dependency_count=(
            dependency_result
            .total_dependency_count
        ),

        confirmed_dependency_count=(
            dependency_result
            .confirmed_dependency_count
        ),

        ambiguous_dependency_count=(
            dependency_result
            .ambiguous_dependency_count
        ),

        dependency_via_table_count=(
            dependency_result
            .via_table_count
        ),

        files=tuple(file_results),
    )


# ============================================================
# 3. 在当前 Session 中导入整个目录
# ============================================================

def ingest_sql_directory(
    db: Session,
    project_id: int,
    root_directory: str | Path,
    dialect_override: str | None = None,
) -> SqlDirectoryIngestionResult:
    """
    在当前数据库 Session 中导入整个 SQL 目录。

    正确顺序：

    1. 检查项目；
    2. 扫描全部 SQL 文件；
    3. 循环处理全部文件；
    4. 循环结束后重建依赖；
    5. 汇总并返回结果。

    本函数只执行 flush，不执行 commit。
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
    # 2. 标准化扫描目录
    # --------------------------------------------------------

    resolved_root = (
        Path(root_directory)
        .expanduser()
        .resolve()
    )

    # --------------------------------------------------------
    # 3. 一次性扫描全部 SQL 文件
    # --------------------------------------------------------

    scanned_files = scan_sql_files(
        resolved_root

    )



    file_results: list[
        SqlFileIngestionResult
    ] = []

    # --------------------------------------------------------
    # 4. 循环处理全部 SQL 文件
    # --------------------------------------------------------

    for scanned_file in scanned_files:

        file_result = ingest_scanned_sql_file(
            db=db,
            project_id=project_id,
            scanned_file=scanned_file,
            dialect_override=dialect_override,
        )

        file_results.append(
            file_result
        )

    # --------------------------------------------------------
    # 5. 所有文件处理完以后，才重建依赖
    # --------------------------------------------------------

    dependency_result = (
        rebuild_script_dependencies(
            db=db,
            project_id=project_id,
        )
    )

    # --------------------------------------------------------
    # 6. 所有文件处理完以后，才返回汇总结果
    # --------------------------------------------------------

    return _build_directory_result(
        root_directory=resolved_root,
        file_results=file_results,
        dependency_result=dependency_result,
    )
# ============================================================
# 4. 使用独立事务导入整个目录
# ============================================================

def ingest_sql_directory_transactionally(
    project_id: int,
    root_directory: str | Path,
    dialect_override: str | None = None,
    session_factory: Callable[
        [],
        Session,
    ] = SessionLocal,
) -> SqlDirectoryIngestionResult:
    """
    使用独立数据库事务导入整个 SQL 目录。

    成功流程：

        创建 Session
            ↓
        导入全部文件
            ↓
        commit
            ↓
        关闭 Session

    异常流程：

        创建 Session
            ↓
        导入过程中发生系统异常
            ↓
        rollback
            ↓
        继续抛出原异常
            ↓
        关闭 Session

    注意：

    SQL 本身解析失败不会抛出异常。
    它会记录为 parse_status=failed，
    因此相关错误信息仍然会保存到数据库。
    """

    db = session_factory()

    try:
        result = ingest_sql_directory(
            db=db,
            project_id=project_id,
            root_directory=root_directory,
            dialect_override=dialect_override,
        )

        # 整个目录只在这里提交一次。
        db.commit()

        return result

    except Exception:

        # 任何未处理的系统异常都会回滚整批数据。
        db.rollback()

        # 继续抛出异常，不能悄悄吞掉错误。
        raise

    finally:
        db.close()
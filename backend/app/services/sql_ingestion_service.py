from __future__ import annotations
from app.services.script_column_lineage_service import (
    process_script_column_lineage,
)
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DataTable,
    LineageProject,
    ScriptTableAccess,
    SourceScript,
)
from app.services.sql_dialect_detector import (
    SUPPORTED_DIALECTS,
    detect_sql_dialect,
)
from app.services.sql_file_scanner import (
    ScannedSqlFile,
)
from app.services.sql_parser import (
    parse_sql_script,
)
from app.services.table_access_extractor import (
    ExtractedTableAccess,
    extract_script_table_accesses,
)


# ============================================================
# 1. SQL 文件导入结果
# ============================================================

@dataclass(frozen=True, slots=True)
class SqlFileIngestionResult:
    """
    一个 SQL 文件导入数据库后的结果。
    """

    # SourceScript 主键
    script_id: int

    # True 表示新建脚本，False 表示更新已有脚本
    created_script: bool

    # SQL 文件相对路径
    relative_path: str

    # 识别出的业务方言
    dialect: str

    # success 或 failed
    parse_status: str

    # SQL 语句总数
    statement_count: int

    # 成功解析的语句数
    success_statement_count: int

    # 失败的语句数
    failed_statement_count: int

    # 保存的 READ/WRITE 记录数
    table_access_count: int


# ============================================================
# 2. 查找或创建 SourceScript
# ============================================================

def _get_or_create_source_script(
    db: Session,
    project_id: int,
    scanned_file: ScannedSqlFile,
) -> tuple[SourceScript, bool]:
    """
    根据下面两个字段判断脚本是否已经存在：

        project_id
        relative_path

    同一个项目中的同一路径只保存一条 SourceScript。
    """

    source_script = db.scalar(
        select(SourceScript).where(
            SourceScript.project_id
            == project_id,

            SourceScript.relative_path
            == scanned_file.relative_path,
        )
    )

    if source_script is not None:
        return source_script, False

    source_script = SourceScript(
        project_id=project_id,
        file_name=scanned_file.file_name,
        relative_path=scanned_file.relative_path,
        dialect="unknown",
        file_hash=scanned_file.file_hash,
        source_code=scanned_file.source_code,
        parse_status="pending",
        parse_error=None,
    )

    db.add(source_script)
    db.flush()

    return source_script, True


# ============================================================
# 3. 查找或创建 DataTable
# ============================================================

def _get_or_create_data_table(
    db: Session,
    project_id: int,
    extracted_access: ExtractedTableAccess,
) -> DataTable:
    """
    根据下面两个字段判断数据表是否已经存在：

        project_id
        full_name

    同一个项目中的同一张表只保存一条 DataTable。
    """

    data_table = db.scalar(
        select(DataTable).where(
            DataTable.project_id
            == project_id,

            DataTable.full_name
            == extracted_access.full_name,
        )
    )

    if data_table is not None:
        return data_table

    data_table = DataTable(
        project_id=project_id,
        catalog_name=(
            extracted_access.catalog_name
        ),
        schema_name=(
            extracted_access.schema_name
        ),
        table_name=(
            extracted_access.table_name
        ),
        full_name=(
            extracted_access.full_name
        ),

        # 仅从 SQL 引用无法确认它究竟是表还是视图，
        # 因此不能强行猜测为 physical。
        table_kind="unknown",
    )

    db.add(data_table)
    db.flush()

    return data_table


# ============================================================
# 4. 删除脚本以前的表访问结果
# ============================================================

def _delete_old_table_accesses(
    db: Session,
    script_id: int,
) -> None:
    """
    重新分析同一个 SQL 文件时，
    删除这个脚本以前的 READ/WRITE 结果。

    注意：

    这里只删除 ScriptTableAccess，
    不删除 DataTable。

    因为同一张 DataTable 可能还被其他脚本引用。
    """

    old_accesses = db.scalars(
        select(ScriptTableAccess).where(
            ScriptTableAccess.script_id
            == script_id
        )
    ).all()

    for old_access in old_accesses:
        db.delete(old_access)

    # 立即执行删除，避免后面重新插入时
    # 与唯一约束产生冲突。
    db.flush()


# ============================================================
# 5. 保存新的表访问结果
# ============================================================

def _save_table_accesses(
    db: Session,
    project_id: int,
    script_id: int,
    extracted_accesses: tuple[
        ExtractedTableAccess,
        ...
    ],
) -> int:
    """
    保存当前脚本最新的 READ/WRITE 结果。

    返回实际保存的访问记录数量。
    """

    saved_count = 0

    for extracted_access in extracted_accesses:

        data_table = _get_or_create_data_table(
            db=db,
            project_id=project_id,
            extracted_access=extracted_access,
        )

        script_table_access = ScriptTableAccess(
            script_id=script_id,
            table_id=data_table.id,
            access_type=(
                extracted_access.access_type
            ),
            statement_no=(
                extracted_access.statement_no
            ),
            line_start=(
                extracted_access.line_start
            ),
            line_end=(
                extracted_access.line_end
            ),
            evidence_sql=(
                extracted_access.evidence_sql
            ),
        )

        db.add(script_table_access)
        saved_count += 1

    db.flush()

    return saved_count


# ============================================================
# 6. 分析并保存一个 SQL 文件
# ============================================================

def ingest_scanned_sql_file(
    db: Session,
    project_id: int,
    scanned_file: ScannedSqlFile,
    dialect_override: str | None = None,
) -> SqlFileIngestionResult:
    """
    对一个已经扫描到的 SQL 文件执行完整处理：

    1. 检查项目是否存在；
    2. 识别 SQL 方言；
    3. 解析多条 SQL；
    4. 提取 READ/WRITE 表；
    5. 新建或更新 SourceScript；
    6. 新建或复用 DataTable；
    7. 替换 ScriptTableAccess；
    8. flush 到数据库。

    本函数不执行 commit。

    最终是否提交事务，由上层调用者决定。
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
    # 2. 确定 SQL 方言
    # --------------------------------------------------------

    if dialect_override is None:

        dialect_result = detect_sql_dialect(
            source_code=scanned_file.source_code,
            relative_path=scanned_file.relative_path,
        )

        business_dialect = (
            dialect_result.dialect
        )

    else:

        if dialect_override not in (
                SUPPORTED_DIALECTS
        ):
            raise ValueError(
                "不支持的指定方言："
                f"{dialect_override}。"
                "允许值为："
                f"{', '.join(SUPPORTED_DIALECTS)}"
            )

        business_dialect = dialect_override

    # --------------------------------------------------------
    # 3. SQLGlot 多语句解析
    # --------------------------------------------------------

    parse_result = parse_sql_script(
        source_code=scanned_file.source_code,
        business_dialect=business_dialect,
    )

    # --------------------------------------------------------
    # 4. 提取 READ / WRITE
    # --------------------------------------------------------

    extracted_accesses = (
        extract_script_table_accesses(
            parse_result
        )
    )

    # --------------------------------------------------------
    # 5. 新建或者取得 SourceScript
    # --------------------------------------------------------

    source_script, created_script = (
        _get_or_create_source_script(
            db=db,
            project_id=project_id,
            scanned_file=scanned_file,
        )
    )

    # --------------------------------------------------------
    # 6. 更新脚本的最新状态
    # --------------------------------------------------------

    source_script.file_name = (
        scanned_file.file_name
    )

    source_script.relative_path = (
        scanned_file.relative_path
    )

    source_script.dialect = (
        business_dialect
    )

    source_script.file_hash = (
        scanned_file.file_hash
    )

    source_script.source_code = (
        scanned_file.source_code
    )

    source_script.parse_status = (
        parse_result.parse_status
    )

    source_script.parse_error = (
        parse_result.parse_error
    )

    db.flush()

    # --------------------------------------------------------
    # 7. 删除旧的 READ / WRITE
    # --------------------------------------------------------

    _delete_old_table_accesses(
        db=db,
        script_id=source_script.id,
    )

    # --------------------------------------------------------
    # 8. 保存新的 READ / WRITE
    # --------------------------------------------------------

    table_access_count = (
        _save_table_accesses(
            db=db,
            project_id=project_id,
            script_id=source_script.id,
            extracted_accesses=(
                extracted_accesses
            ),
        )
    )

    # --------------------------------------------------------
    # 自动生成字段级血缘
    # --------------------------------------------------------
    if parse_result.parse_status == "success":
        process_script_column_lineage(
            db=db,
            script_id=source_script.id,
        )
    # --------------------------------------------------------
    # 9. 返回本次处理摘要
    # --------------------------------------------------------

    return SqlFileIngestionResult(
        script_id=source_script.id,
        created_script=created_script,
        relative_path=(
            scanned_file.relative_path
        ),
        dialect=business_dialect,
        parse_status=(
            parse_result.parse_status
        ),
        statement_count=(
            parse_result.total_count
        ),
        success_statement_count=(
            parse_result.success_count
        ),
        failed_statement_count=(
            parse_result.failed_count
        ),
        table_access_count=(
            table_access_count
        ),
    )
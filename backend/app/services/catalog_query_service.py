from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
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
# 一、项目列表返回结构
# ============================================================

@dataclass(frozen=True)
class ProjectListItem:
    """
    项目列表中的一条项目记录。

    除了项目自身信息，还返回该项目下面的：

    - SQL 脚本数量
    - 数据表数量
    - 脚本依赖数量
    """

    project_id: int
    name: str
    description: str | None

    script_count: int
    table_count: int
    dependency_count: int

    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProjectListResult:
    """
    项目分页查询结果。
    """

    page: int
    page_size: int
    total: int
    items: tuple[ProjectListItem, ...]


# ============================================================
# 二、脚本列表返回结构
# ============================================================

@dataclass(frozen=True)
class ScriptListItem:
    """
    项目脚本列表中的一条记录。
    """

    script_id: int

    file_name: str
    relative_path: str

    dialect: str
    parse_status: str
    parse_error: str | None

    # 当前脚本读取了多少张不同的数据表
    read_table_count: int

    # 当前脚本写入了多少张不同的数据表
    write_table_count: int

    # 有多少个上游脚本依赖
    upstream_dependency_count: int

    # 有多少个下游脚本依赖
    downstream_dependency_count: int

    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ScriptListResult:
    """
    指定项目下的脚本分页查询结果。
    """

    project_id: int
    project_name: str

    page: int
    page_size: int
    total: int

    items: tuple[ScriptListItem, ...]


# ============================================================
# 三、数据表列表返回结构
# ============================================================

@dataclass(frozen=True)
class TableListItem:
    """
    项目数据表列表中的一条记录。
    """

    table_id: int

    catalog_name: str | None
    schema_name: str | None
    table_name: str
    full_name: str
    table_kind: str

    # 当前表已经登记的字段数量
    column_count: int

    # 有多少个不同脚本写入当前表
    writer_script_count: int

    # 有多少个不同脚本读取当前表
    reader_script_count: int

    # 有多少条脚本依赖通过当前表建立
    dependency_count: int

    created_at: datetime


@dataclass(frozen=True)
class TableListResult:
    """
    指定项目下的数据表分页查询结果。
    """

    project_id: int
    project_name: str

    page: int
    page_size: int
    total: int

    items: tuple[TableListItem, ...]


# ============================================================
# 四、通用辅助函数
# ============================================================

def _validate_pagination(
    page: int,
    page_size: int,
) -> None:
    """
    检查分页参数是否合法。

    page:
        必须从 1 开始。

    page_size:
        最小为 1；
        最大为 100，防止一次读取过多记录。
    """

    if page < 1:
        raise ValueError(
            "page 必须大于或等于 1"
        )

    if page_size < 1:
        raise ValueError(
            "page_size 必须大于或等于 1"
        )

    if page_size > 100:
        raise ValueError(
            "page_size 不能大于 100"
        )


def _normalize_keyword(
    keyword: str | None,
) -> str | None:
    """
    清理可选的搜索关键字。

    例如：

        None
        ""

        "   "

    都会被转换为 None。

    而：

        " orders "

    会被转换为：

        "orders"
    """

    if keyword is None:
        return None

    normalized_keyword = keyword.strip()

    if not normalized_keyword:
        return None

    return normalized_keyword


def _get_project_or_raise(
    db: Session,
    project_id: int,
) -> LineageProject:
    """
    根据 project_id 查询项目。

    如果项目不存在，统一抛出 ValueError。
    路由层可以把这个错误转换成 HTTP 404。
    """

    project = db.get(
        LineageProject,
        project_id,
    )

    if project is None:
        raise ValueError(
            f"血缘项目不存在：project_id={project_id}"
        )

    return project


# ============================================================
# 五、项目统计辅助查询
# ============================================================

def _get_project_script_counts(
    db: Session,
    project_ids: list[int],
) -> dict[int, int]:
    """
    批量查询每个项目拥有多少个脚本。

    返回格式：

        {
            project_id: script_count
        }
    """

    if not project_ids:
        return {}

    rows = db.execute(
        select(
            SourceScript.project_id,
            func.count(SourceScript.id),
        )
        .where(
            SourceScript.project_id.in_(
                project_ids
            )
        )
        .group_by(
            SourceScript.project_id
        )
    ).all()

    return {
        project_id: int(script_count)
        for project_id, script_count in rows
    }


def _get_project_table_counts(
    db: Session,
    project_ids: list[int],
) -> dict[int, int]:
    """
    批量查询每个项目拥有多少张数据表。
    """

    if not project_ids:
        return {}

    rows = db.execute(
        select(
            DataTable.project_id,
            func.count(DataTable.id),
        )
        .where(
            DataTable.project_id.in_(
                project_ids
            )
        )
        .group_by(
            DataTable.project_id
        )
    ).all()

    return {
        project_id: int(table_count)
        for project_id, table_count in rows
    }


def _get_project_dependency_counts(
    db: Session,
    project_ids: list[int],
) -> dict[int, int]:
    """
    批量查询每个项目拥有多少条脚本依赖。
    """

    if not project_ids:
        return {}

    rows = db.execute(
        select(
            ScriptDependency.project_id,
            func.count(ScriptDependency.id),
        )
        .where(
            ScriptDependency.project_id.in_(
                project_ids
            )
        )
        .group_by(
            ScriptDependency.project_id
        )
    ).all()

    return {
        project_id: int(dependency_count)
        for project_id, dependency_count in rows
    }


# ============================================================
# 六、脚本统计辅助查询
# ============================================================

def _get_script_table_access_counts(
    db: Session,
    script_ids: list[int],
    access_type: str,
) -> dict[int, int]:
    """
    批量统计每个脚本读取或写入了多少张不同的数据表。

    access_type 只能是：

        read
        write

    这里使用 DISTINCT table_id。

    原因是同一个脚本可能在多个 SQL 语句中重复访问同一张表，
    但列表页面通常应该显示“不同数据表数量”，而不是访问记录数量。
    """

    if not script_ids:
        return {}

    if access_type not in {
        "read",
        "write",
    }:
        raise ValueError(
            "access_type 必须是 read 或 write"
        )

    rows = db.execute(
        select(
            ScriptTableAccess.script_id,
            func.count(
                func.distinct(
                    ScriptTableAccess.table_id
                )
            ),
        )
        .where(
            ScriptTableAccess.script_id.in_(
                script_ids
            ),
            ScriptTableAccess.access_type
            == access_type,
        )
        .group_by(
            ScriptTableAccess.script_id
        )
    ).all()

    return {
        script_id: int(table_count)
        for script_id, table_count in rows
    }


def _get_upstream_dependency_counts(
    db: Session,
    script_ids: list[int],
) -> dict[int, int]:
    """
    查询每个脚本拥有多少个不同的上游脚本。

    如果依赖关系是：

        A -> B

    那么：

        A 是 B 的上游脚本
        B 是 A 的下游脚本

    当前脚本作为 downstream_script 时，
    统计 upstream_script_id。
    """

    if not script_ids:
        return {}

    rows = db.execute(
        select(
            ScriptDependency.downstream_script_id,
            func.count(
                func.distinct(
                    ScriptDependency.upstream_script_id
                )
            ),
        )
        .where(
            ScriptDependency.downstream_script_id.in_(
                script_ids
            )
        )
        .group_by(
            ScriptDependency.downstream_script_id
        )
    ).all()

    return {
        script_id: int(dependency_count)
        for script_id, dependency_count in rows
    }


def _get_downstream_dependency_counts(
    db: Session,
    script_ids: list[int],
) -> dict[int, int]:
    """
    查询每个脚本拥有多少个不同的下游脚本。

    当前脚本作为 upstream_script 时，
    统计 downstream_script_id。
    """

    if not script_ids:
        return {}

    rows = db.execute(
        select(
            ScriptDependency.upstream_script_id,
            func.count(
                func.distinct(
                    ScriptDependency.downstream_script_id
                )
            ),
        )
        .where(
            ScriptDependency.upstream_script_id.in_(
                script_ids
            )
        )
        .group_by(
            ScriptDependency.upstream_script_id
        )
    ).all()

    return {
        script_id: int(dependency_count)
        for script_id, dependency_count in rows
    }


# ============================================================
# 七、数据表统计辅助查询
# ============================================================

def _get_table_column_counts(
    db: Session,
    table_ids: list[int],
) -> dict[int, int]:
    """
    批量查询每张数据表的字段数量。
    """

    if not table_ids:
        return {}

    rows = db.execute(
        select(
            DataColumn.table_id,
            func.count(DataColumn.id),
        )
        .where(
            DataColumn.table_id.in_(
                table_ids
            )
        )
        .group_by(
            DataColumn.table_id
        )
    ).all()

    return {
        table_id: int(column_count)
        for table_id, column_count in rows
    }


def _get_table_script_counts(
    db: Session,
    table_ids: list[int],
    access_type: str,
) -> dict[int, int]:
    """
    批量统计每张表被多少个不同脚本读取或写入。

    access_type:

        read
        write

    这里使用 DISTINCT script_id，
    防止同一个脚本多次访问同一张表时被重复统计。
    """

    if not table_ids:
        return {}

    if access_type not in {
        "read",
        "write",
    }:
        raise ValueError(
            "access_type 必须是 read 或 write"
        )

    rows = db.execute(
        select(
            ScriptTableAccess.table_id,
            func.count(
                func.distinct(
                    ScriptTableAccess.script_id
                )
            ),
        )
        .where(
            ScriptTableAccess.table_id.in_(
                table_ids
            ),
            ScriptTableAccess.access_type
            == access_type,
        )
        .group_by(
            ScriptTableAccess.table_id
        )
    ).all()

    return {
        table_id: int(script_count)
        for table_id, script_count in rows
    }


def _get_table_dependency_counts(
    db: Session,
    table_ids: list[int],
) -> dict[int, int]:
    """
    批量查询每张表建立了多少条脚本依赖。

    ScriptDependency.via_table_id 表示：

        upstream_script
                ↓
            via_table
                ↓
        downstream_script
    """

    if not table_ids:
        return {}

    rows = db.execute(
        select(
            ScriptDependency.via_table_id,
            func.count(ScriptDependency.id),
        )
        .where(
            ScriptDependency.via_table_id.in_(
                table_ids
            )
        )
        .group_by(
            ScriptDependency.via_table_id
        )
    ).all()

    return {
        table_id: int(dependency_count)
        for table_id, dependency_count in rows
    }


# ============================================================
# 八、查询项目列表
# ============================================================

def list_projects(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
) -> ProjectListResult:
    """
    分页查询血缘项目列表。

    keyword 会搜索：

        lineage_project.name
        lineage_project.description
    """

    _validate_pagination(
        page=page,
        page_size=page_size,
    )

    normalized_keyword = _normalize_keyword(
        keyword
    )

    filters = []

    if normalized_keyword is not None:
        keyword_pattern = (
            f"%{normalized_keyword}%"
        )

        filters.append(
            or_(
                LineageProject.name.like(
                    keyword_pattern
                ),
                LineageProject.description.like(
                    keyword_pattern
                ),
            )
        )

    # --------------------------------------------------------
    # 1. 查询符合条件的记录总数
    # --------------------------------------------------------

    total_statement = (
        select(
            func.count(LineageProject.id)
        )
        .select_from(LineageProject)
    )

    if filters:
        total_statement = (
            total_statement.where(*filters)
        )

    total = int(
        db.scalar(total_statement) or 0
    )

    # --------------------------------------------------------
    # 2. 查询当前分页中的项目
    # --------------------------------------------------------

    offset = (
        page - 1
    ) * page_size

    project_statement = (
        select(LineageProject)
        .order_by(
            LineageProject.created_at.desc(),
            LineageProject.id.desc(),
        )
        .offset(offset)
        .limit(page_size)
    )

    if filters:
        project_statement = (
            project_statement.where(*filters)
        )

    projects = list(
        db.scalars(
            project_statement
        ).all()
    )

    # --------------------------------------------------------
    # 3. 批量查询项目统计数据
    # --------------------------------------------------------

    project_ids = [
        project.id
        for project in projects
    ]

    script_counts = (
        _get_project_script_counts(
            db=db,
            project_ids=project_ids,
        )
    )

    table_counts = (
        _get_project_table_counts(
            db=db,
            project_ids=project_ids,
        )
    )

    dependency_counts = (
        _get_project_dependency_counts(
            db=db,
            project_ids=project_ids,
        )
    )

    # --------------------------------------------------------
    # 4. 组装返回结果
    # --------------------------------------------------------

    items = tuple(
        ProjectListItem(
            project_id=project.id,
            name=project.name,
            description=project.description,
            script_count=script_counts.get(
                project.id,
                0,
            ),
            table_count=table_counts.get(
                project.id,
                0,
            ),
            dependency_count=dependency_counts.get(
                project.id,
                0,
            ),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
        for project in projects
    )

    return ProjectListResult(
        page=page,
        page_size=page_size,
        total=total,
        items=items,
    )


# ============================================================
# 九、查询指定项目的脚本列表
# ============================================================

def list_project_scripts(
    db: Session,
    project_id: int,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    parse_status: str | None = None,
) -> ScriptListResult:
    """
    分页查询指定项目下的 SQL 脚本。

    keyword 会搜索：

        source_script.file_name
        source_script.relative_path

    parse_status 可以筛选：

        pending
        success
        failed
    """

    _validate_pagination(
        page=page,
        page_size=page_size,
    )

    project = _get_project_or_raise(
        db=db,
        project_id=project_id,
    )

    normalized_keyword = _normalize_keyword(
        keyword
    )

    normalized_parse_status = (
        _normalize_keyword(parse_status)
    )

    filters = [
        SourceScript.project_id
        == project_id
    ]

    if normalized_keyword is not None:
        keyword_pattern = (
            f"%{normalized_keyword}%"
        )

        filters.append(
            or_(
                SourceScript.file_name.like(
                    keyword_pattern
                ),
                SourceScript.relative_path.like(
                    keyword_pattern
                ),
            )
        )

    if normalized_parse_status is not None:
        filters.append(
            SourceScript.parse_status
            == normalized_parse_status
        )

    # --------------------------------------------------------
    # 1. 查询总数
    # --------------------------------------------------------

    total_statement = (
        select(
            func.count(SourceScript.id)
        )
        .select_from(SourceScript)
        .where(*filters)
    )

    total = int(
        db.scalar(total_statement) or 0
    )

    # --------------------------------------------------------
    # 2. 查询当前页脚本
    # --------------------------------------------------------

    offset = (
        page - 1
    ) * page_size

    scripts = list(
        db.scalars(
            select(SourceScript)
            .where(*filters)
            .order_by(
                SourceScript.relative_path.asc(),
                SourceScript.id.asc(),
            )
            .offset(offset)
            .limit(page_size)
        ).all()
    )

    # --------------------------------------------------------
    # 3. 批量查询脚本统计数据
    # --------------------------------------------------------

    script_ids = [
        script.id
        for script in scripts
    ]

    read_table_counts = (
        _get_script_table_access_counts(
            db=db,
            script_ids=script_ids,
            access_type="read",
        )
    )

    write_table_counts = (
        _get_script_table_access_counts(
            db=db,
            script_ids=script_ids,
            access_type="write",
        )
    )

    upstream_dependency_counts = (
        _get_upstream_dependency_counts(
            db=db,
            script_ids=script_ids,
        )
    )

    downstream_dependency_counts = (
        _get_downstream_dependency_counts(
            db=db,
            script_ids=script_ids,
        )
    )

    # --------------------------------------------------------
    # 4. 组装返回结果
    # --------------------------------------------------------

    items = tuple(
        ScriptListItem(
            script_id=script.id,
            file_name=script.file_name,
            relative_path=script.relative_path,
            dialect=script.dialect,
            parse_status=script.parse_status,
            parse_error=script.parse_error,
            read_table_count=read_table_counts.get(
                script.id,
                0,
            ),
            write_table_count=write_table_counts.get(
                script.id,
                0,
            ),
            upstream_dependency_count=(
                upstream_dependency_counts.get(
                    script.id,
                    0,
                )
            ),
            downstream_dependency_count=(
                downstream_dependency_counts.get(
                    script.id,
                    0,
                )
            ),
            created_at=script.created_at,
            updated_at=script.updated_at,
        )
        for script in scripts
    )

    return ScriptListResult(
        project_id=project.id,
        project_name=project.name,
        page=page,
        page_size=page_size,
        total=total,
        items=items,
    )


# ============================================================
# 十、查询指定项目的数据表列表
# ============================================================

def list_project_tables(
    db: Session,
    project_id: int,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    table_kind: str | None = None,
) -> TableListResult:
    """
    分页查询指定项目下的数据表。

    keyword 会搜索：

        data_table.catalog_name
        data_table.schema_name
        data_table.table_name
        data_table.full_name

    table_kind 可以筛选：

        physical
        view
        temp
        unknown
    """

    _validate_pagination(
        page=page,
        page_size=page_size,
    )

    project = _get_project_or_raise(
        db=db,
        project_id=project_id,
    )

    normalized_keyword = _normalize_keyword(
        keyword
    )

    normalized_table_kind = (
        _normalize_keyword(table_kind)
    )

    filters = [
        DataTable.project_id
        == project_id
    ]

    if normalized_keyword is not None:
        keyword_pattern = (
            f"%{normalized_keyword}%"
        )

        filters.append(
            or_(
                DataTable.catalog_name.like(
                    keyword_pattern
                ),
                DataTable.schema_name.like(
                    keyword_pattern
                ),
                DataTable.table_name.like(
                    keyword_pattern
                ),
                DataTable.full_name.like(
                    keyword_pattern
                ),
            )
        )

    if normalized_table_kind is not None:
        filters.append(
            DataTable.table_kind
            == normalized_table_kind
        )

    # --------------------------------------------------------
    # 1. 查询总数
    # --------------------------------------------------------

    total_statement = (
        select(
            func.count(DataTable.id)
        )
        .select_from(DataTable)
        .where(*filters)
    )

    total = int(
        db.scalar(total_statement) or 0
    )

    # --------------------------------------------------------
    # 2. 查询当前页数据表
    # --------------------------------------------------------

    offset = (
        page - 1
    ) * page_size

    tables = list(
        db.scalars(
            select(DataTable)
            .where(*filters)
            .order_by(
                DataTable.full_name.asc(),
                DataTable.id.asc(),
            )
            .offset(offset)
            .limit(page_size)
        ).all()
    )

    # --------------------------------------------------------
    # 3. 批量查询数据表统计信息
    # --------------------------------------------------------

    table_ids = [
        table.id
        for table in tables
    ]

    column_counts = (
        _get_table_column_counts(
            db=db,
            table_ids=table_ids,
        )
    )

    writer_script_counts = (
        _get_table_script_counts(
            db=db,
            table_ids=table_ids,
            access_type="write",
        )
    )

    reader_script_counts = (
        _get_table_script_counts(
            db=db,
            table_ids=table_ids,
            access_type="read",
        )
    )

    dependency_counts = (
        _get_table_dependency_counts(
            db=db,
            table_ids=table_ids,
        )
    )

    # --------------------------------------------------------
    # 4. 组装返回结果
    # --------------------------------------------------------

    items = tuple(
        TableListItem(
            table_id=table.id,
            catalog_name=table.catalog_name,
            schema_name=table.schema_name,
            table_name=table.table_name,
            full_name=table.full_name,
            table_kind=table.table_kind,
            column_count=column_counts.get(
                table.id,
                0,
            ),
            writer_script_count=(
                writer_script_counts.get(
                    table.id,
                    0,
                )
            ),
            reader_script_count=(
                reader_script_counts.get(
                    table.id,
                    0,
                )
            ),
            dependency_count=(
                dependency_counts.get(
                    table.id,
                    0,
                )
            ),
            created_at=table.created_at,
        )
        for table in tables
    )

    return TableListResult(
        project_id=project.id,
        project_name=project.name,
        page=page,
        page_size=page_size,
        total=total,
        items=items,
    )
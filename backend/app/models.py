from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================================
# 1. 血缘项目
# ============================================================

class LineageProject(Base):
    """
    一次完整的数据血缘分析项目。

    一个项目可以包含：
    - 多个源代码脚本；
    - 多张数据表；
    - 多条脚本依赖；
    - 多条字段血缘。
    """

    __tablename__ = "lineage_project"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    scripts: Mapped[list["SourceScript"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    tables: Mapped[list["DataTable"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    dependencies: Mapped[list["ScriptDependency"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    column_lineages: Mapped[list["ColumnLineage"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# ============================================================
# 2. 源代码脚本
# ============================================================

class SourceScript(Base):
    """
    用户导入的数据开发脚本。

    保存：
    - 文件名；
    - 相对路径；
    - SQL 方言；
    - 文件 Hash；
    - 原始代码；
    - 解析状态；
    - 解析错误。
    """

    __tablename__ = "source_script"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "relative_path",
            name="uq_source_script_project_path",
        ),
        CheckConstraint(
            (
                "dialect IN "
                "('hive', 'spark', 'mysql', "
                "'postgresql', 'unknown')"
            ),
            name="source_script_dialect",
        ),
        CheckConstraint(
            (
                "parse_status IN "
                "('pending', 'success', 'failed')"
            ),
            name="source_script_parse_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey(
            "lineage_project.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    relative_path: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    dialect: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="unknown",
    )

    file_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    source_code: Mapped[str] = mapped_column(
        LONGTEXT,
        nullable=False,
    )

    parse_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )

    parse_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    project: Mapped["LineageProject"] = relationship(
        back_populates="scripts",
    )

    table_accesses: Mapped[list["ScriptTableAccess"]] = relationship(
        back_populates="script",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    upstream_dependencies: Mapped[
        list["ScriptDependency"]
    ] = relationship(
        foreign_keys="ScriptDependency.downstream_script_id",
        back_populates="downstream_script",
        passive_deletes=True,
    )

    downstream_dependencies: Mapped[
        list["ScriptDependency"]
    ] = relationship(
        foreign_keys="ScriptDependency.upstream_script_id",
        back_populates="upstream_script",
        passive_deletes=True,
    )

    column_lineages: Mapped[list["ColumnLineage"]] = relationship(
        back_populates="script",
        passive_deletes=True,
    )

    evidences: Mapped[list["LineageEvidence"]] = relationship(
        back_populates="script",
        passive_deletes=True,
    )


# ============================================================
# 3. 数据表
# ============================================================

class DataTable(Base):
    """
    从 SQL 脚本中识别出来的数据表。

    可以表示：
    - 物理表；
    - 视图；
    - 临时表；
    - 暂时不能判断类型的表。
    """

    __tablename__ = "data_table"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "full_name",
            name="uq_data_table_project_full_name",
        ),
        CheckConstraint(
            (
                "table_kind IN "
                "('physical', 'view', 'temp', 'unknown')"
            ),
            name="data_table_kind",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey(
            "lineage_project.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    catalog_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    schema_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    table_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    full_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    table_kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="unknown",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped["LineageProject"] = relationship(
        back_populates="tables",
    )

    columns: Mapped[list["DataColumn"]] = relationship(
        back_populates="table",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    script_accesses: Mapped[
        list["ScriptTableAccess"]
    ] = relationship(
        back_populates="table",
        passive_deletes=True,
    )

    dependencies: Mapped[
        list["ScriptDependency"]
    ] = relationship(
        back_populates="via_table",
        passive_deletes=True,
    )


# ============================================================
# 4. 数据字段
# ============================================================

class DataColumn(Base):
    """
    数据表中的字段。

    字段必须属于某一张 DataTable。
    """

    __tablename__ = "data_column"

    __table_args__ = (
        UniqueConstraint(
            "table_id",
            "column_name",
            name="uq_data_column_table_column",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    table_id: Mapped[int] = mapped_column(
        ForeignKey(
            "data_table.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    column_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    ordinal_position: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    data_type: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    table: Mapped["DataTable"] = relationship(
        back_populates="columns",
    )

    as_target_lineages: Mapped[
        list["ColumnLineage"]
    ] = relationship(
        foreign_keys="ColumnLineage.target_column_id",
        back_populates="target_column",
        passive_deletes=True,
    )

    as_source_lineages: Mapped[
        list["ColumnLineage"]
    ] = relationship(
        foreign_keys="ColumnLineage.source_column_id",
        back_populates="source_column",
        passive_deletes=True,
    )


# ============================================================
# 5. 脚本读写表关系
# ============================================================

class ScriptTableAccess(Base):
    """
    描述一个脚本读取或写入了哪一张数据表。

    示例：
        ods_to_dwd.sql READ  ods.orders
        ods_to_dwd.sql WRITE dwd.orders
    """

    __tablename__ = "script_table_access"

    __table_args__ = (
        UniqueConstraint(
            "script_id",
            "table_id",
            "access_type",
            "statement_no",
            name="uq_script_table_access",
        ),
        CheckConstraint(
            "access_type IN ('read', 'write')",
            name="script_table_access_type",
        ),
        CheckConstraint(
            "statement_no >= 1",
            name="script_table_access_statement_no",
        ),
        Index(
            "ix_script_table_access_table_type",
            "table_id",
            "access_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    script_id: Mapped[int] = mapped_column(
        ForeignKey(
            "source_script.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    table_id: Mapped[int] = mapped_column(
        ForeignKey(
            "data_table.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    access_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    statement_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    line_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    line_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    evidence_sql: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    script: Mapped["SourceScript"] = relationship(
        back_populates="table_accesses",
    )

    table: Mapped["DataTable"] = relationship(
        back_populates="script_accesses",
    )


# ============================================================
# 6. 脚本依赖
# ============================================================

class ScriptDependency(Base):
    """
    脚本级依赖关系。

    upstream_script
           ↓
       via_table
           ↓
    downstream_script
    """

    __tablename__ = "script_dependency"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "upstream_script_id",
            "downstream_script_id",
            "via_table_id",
            name="uq_script_dependency_path",
        ),
        CheckConstraint(
            (
                "dependency_status IN "
                "('confirmed', 'ambiguous')"
            ),
            name="script_dependency_status",
        ),
        CheckConstraint(
            "upstream_script_id <> downstream_script_id",
            name="script_dependency_not_self",
        ),
        Index(
            "ix_script_dependency_downstream_status",
            "downstream_script_id",
            "dependency_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey(
            "lineage_project.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    upstream_script_id: Mapped[int] = mapped_column(
        ForeignKey(
            "source_script.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    downstream_script_id: Mapped[int] = mapped_column(
        ForeignKey(
            "source_script.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    via_table_id: Mapped[int] = mapped_column(
        ForeignKey(
            "data_table.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    dependency_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="confirmed",
    )

    reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped["LineageProject"] = relationship(
        back_populates="dependencies",
    )

    upstream_script: Mapped["SourceScript"] = relationship(
        foreign_keys=[upstream_script_id],
        back_populates="downstream_dependencies",
    )

    downstream_script: Mapped["SourceScript"] = relationship(
        foreign_keys=[downstream_script_id],
        back_populates="upstream_dependencies",
    )

    via_table: Mapped["DataTable"] = relationship(
        back_populates="dependencies",
    )


# ============================================================
# 7. 字段血缘
# ============================================================

class ColumnLineage(Base):
    """
    字段级血缘关系。

    source_column
          ↓
    expression_text
          ↓
    target_column
    """

    __tablename__ = "column_lineage"

    __table_args__ = (
        CheckConstraint(
            (
                "relation_type IN "
                "('direct', 'transform', 'aggregate', "
                "'constant', 'unknown')"
            ),
            name="column_lineage_relation_type",
        ),
        CheckConstraint(
            (
                "resolution_status IN "
                "('confirmed', 'ambiguous', 'unresolved')"
            ),
            name="column_lineage_resolution_status",
        ),
        CheckConstraint(
            "statement_no >= 1",
            name="column_lineage_statement_no",
        ),
        Index(
            "ix_column_lineage_target_status",
            "target_column_id",
            "resolution_status",
        ),
        Index(
            "ix_column_lineage_source",
            "source_column_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey(
            "lineage_project.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    script_id: Mapped[int] = mapped_column(
        ForeignKey(
            "source_script.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    target_column_id: Mapped[int] = mapped_column(
        ForeignKey(
            "data_column.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    source_column_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "data_column.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    relation_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    resolution_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="confirmed",
    )

    expression_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    statement_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped["LineageProject"] = relationship(
        back_populates="column_lineages",
    )

    script: Mapped["SourceScript"] = relationship(
        back_populates="column_lineages",
    )

    target_column: Mapped["DataColumn"] = relationship(
        foreign_keys=[target_column_id],
        back_populates="as_target_lineages",
    )

    source_column: Mapped["DataColumn | None"] = relationship(
        foreign_keys=[source_column_id],
        back_populates="as_source_lineages",
    )

    evidences: Mapped[list["LineageEvidence"]] = relationship(
        back_populates="column_lineage",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# ============================================================
# 8. 字段血缘证据
# ============================================================

class LineageEvidence(Base):
    """
    字段血缘对应的源码证据。

    保存：
    - 对应脚本；
    - SQL语句编号；
    - 代码所在行；
    - 原始代码片段；
    - 转换表达式。
    """

    __tablename__ = "lineage_evidence"

    __table_args__ = (
        CheckConstraint(
            "statement_no >= 1",
            name="lineage_evidence_statement_no",
        ),
        CheckConstraint(
            "evidence_order >= 1",
            name="lineage_evidence_order",
        ),
        Index(
            "ix_lineage_evidence_lineage_order",
            "column_lineage_id",
            "evidence_order",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    column_lineage_id: Mapped[int] = mapped_column(
        ForeignKey(
            "column_lineage.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    script_id: Mapped[int] = mapped_column(
        ForeignKey(
            "source_script.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    statement_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    evidence_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    line_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    line_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    code_snippet: Mapped[str] = mapped_column(
        LONGTEXT,
        nullable=False,
    )

    expression_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    column_lineage: Mapped["ColumnLineage"] = relationship(
        back_populates="evidences",
    )

    script: Mapped["SourceScript"] = relationship(
        back_populates="evidences",
    )
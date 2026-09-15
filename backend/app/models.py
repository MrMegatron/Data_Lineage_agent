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


# =========================================================
# 1. 项目
# =========================================================

class LineageProject(Base):
    """
    一次完整的数据模型代码集合。

    例如：
        hive_dw_project
        finance_model
        risk_model
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
    )

    tables: Mapped[list["DataTable"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )

    dependencies: Mapped[list["ScriptDependency"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )

    column_lineages: Mapped[list["ColumnLineage"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


# =========================================================
# 2. 源代码脚本
# =========================================================

class SourceScript(Base):
    """
    上传的源码脚本。

    保存：
    - 文件路径
    - SQL 方言
    - 文件 hash
    - 原始代码
    - 解析状态
    """

    __tablename__ = "source_script"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "relative_path",
            name="uq_source_script_project_path",
        ),
        CheckConstraint(
            "dialect IN ('hive', 'spark', 'postgresql', 'unknown')",
            name="source_script_dialect",
        ),
        CheckConstraint(
            "parse_status IN ('pending', 'success', 'failed')",
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
    )

    upstream_dependencies: Mapped[list["ScriptDependency"]] = relationship(
        foreign_keys="ScriptDependency.downstream_script_id",
        back_populates="downstream_script",
    )

    downstream_dependencies: Mapped[list["ScriptDependency"]] = relationship(
        foreign_keys="ScriptDependency.upstream_script_id",
        back_populates="upstream_script",
    )

    column_lineages: Mapped[list["ColumnLineage"]] = relationship(
        back_populates="script",
    )

    evidences: Mapped[list["LineageEvidence"]] = relationship(
        back_populates="script",
    )


# =========================================================
# 3. 数据表
# =========================================================

class DataTable(Base):
    """
    SQL 代码中识别出来的表。
    """

    __tablename__ = "data_table"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "full_name",
            name="uq_data_table_project_full_name",
        ),
        CheckConstraint(
            "table_kind IN ('physical', 'view', 'temp', 'unknown')",
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
    )

    script_accesses: Mapped[list["ScriptTableAccess"]] = relationship(
        back_populates="table",
    )

    dependencies: Mapped[list["ScriptDependency"]] = relationship(
        back_populates="via_table",
    )


# =========================================================
# 4. 数据字段
# =========================================================

class DataColumn(Base):
    """
    表中的字段。
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

    as_target_lineages: Mapped[list["ColumnLineage"]] = relationship(
        foreign_keys="ColumnLineage.target_column_id",
        back_populates="target_column",
    )

    as_source_lineages: Mapped[list["ColumnLineage"]] = relationship(
        foreign_keys="ColumnLineage.source_column_id",
        back_populates="source_column",
    )


# =========================================================
# 5. 脚本 READ / WRITE 表
# =========================================================

class ScriptTableAccess(Base):
    """
    描述一个脚本读取或写入了哪张表。

    这是自动建立脚本依赖的基础。
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


# =========================================================
# 6. 脚本依赖
# =========================================================

class ScriptDependency(Base):
    """
    脚本级 DAG。

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
            "dependency_status IN ('confirmed', 'ambiguous')",
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


# =========================================================
# 7. 字段血缘
# =========================================================

class ColumnLineage(Base):
    """
    字段级血缘。

    source_column
          ↓
    transformation
          ↓
    target_column
    """

    __tablename__ = "column_lineage"

    __table_args__ = (
        CheckConstraint(
            "relation_type IN "
            "('direct', 'transform', 'aggregate', 'constant', 'unknown')",
            name="column_lineage_relation_type",
        ),
        CheckConstraint(
            "resolution_status IN "
            "('confirmed', 'ambiguous', 'unresolved')",
            name="column_lineage_resolution_status",
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
    )


# =========================================================
# 8. 血缘证据
# =========================================================

class LineageEvidence(Base):
    """
    字段血缘对应的源码证据。

    这是整个系统防止 LLM / 程序臆造的重要表。
    """

    __tablename__ = "lineage_evidence"

    __table_args__ = (
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
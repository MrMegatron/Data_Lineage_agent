from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


# ============================================================
# 1. 目录导入请求
# ============================================================

class SqlDirectoryIngestionRequest(BaseModel):
    """
    SQL 目录导入请求。

    relative_directory 必须是 SQL_SOURCE_ROOT
    下面的相对路径。
    """

    relative_directory: str = Field(
        default=".",
        min_length=1,
        max_length=500,
        description=(
            "SQL_SOURCE_ROOT 下的相对目录"
        ),
        examples=[
            "hive_project",
            "finance/2026",
        ],
    )
    dialect: Literal[
        "auto",
        "hive",
        "spark",
        "postgresql",
    ] = Field(
        default="auto",
        description=(
            "SQL 方言。auto 表示自动识别"
        ),
        examples=[
            "hive",
        ],
    )
    @field_validator("relative_directory")
    @classmethod
    def validate_relative_directory(
        cls,
        value: str,
    ) -> str:
        """
        去除首尾空格，并拒绝空字符串和空字符。
        """

        cleaned_value = value.strip()

        if not cleaned_value:
            raise ValueError(
                "relative_directory 不能为空"
            )

        if "\x00" in cleaned_value:
            raise ValueError(
                "relative_directory 包含非法空字符"
            )

        return cleaned_value


# ============================================================
# 2. 单文件导入响应
# ============================================================

class SqlFileIngestionResponse(BaseModel):
    """
    单个 SQL 文件的处理结果。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

    script_id: int
    created_script: bool
    relative_path: str

    dialect: Literal[
        "hive",
        "spark",
        "postgresql",
        "unknown",
    ]

    parse_status: Literal[
        "success",
        "failed",
    ]

    statement_count: int
    success_statement_count: int
    failed_statement_count: int
    table_access_count: int


# ============================================================
# 3. 整个目录导入响应
# ============================================================

class SqlDirectoryIngestionResponse(BaseModel):
    """
    整个 SQL 目录的批量处理结果。
    """

    model_config = ConfigDict(
        from_attributes=True
    )

    root_directory: str

    status: Literal[
        "success",
        "partial",
        "failed",
        "empty",
    ]

    total_files: int
    success_files: int
    failed_files: int

    created_scripts: int
    updated_scripts: int

    total_statements: int
    successful_statements: int
    failed_statements: int

    table_access_count: int
    # 重建前删除的旧依赖数量
    deleted_dependency_count: int

    # 本次生成的依赖总数
    total_dependency_count: int

    # 可以确认的依赖数量
    confirmed_dependency_count: int

    # 存在多个可能上游的依赖数量
    ambiguous_dependency_count: int

    # 产生依赖关系的数据表数量
    dependency_via_table_count: int
    files: list[
        SqlFileIngestionResponse
    ]
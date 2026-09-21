
from __future__ import annotations
from typing import Literal
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.catalog import (
    ProjectListResponse,
    ScriptListResponse,
    TableListResponse,
)
from app.services.catalog_query_service import (
    list_project_scripts,
    list_project_tables,
    list_projects,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api",
    tags=["Catalog"],
)


@router.get(
    "/projects",
    response_model=ProjectListResponse,
    status_code=status.HTTP_200_OK,
    summary="查询项目列表",
)
def list_projects_endpoint(
    page: int = Query(
        default=1,
        ge=1,
        description="页码，从 1 开始",
    ),
    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="每页记录数",
    ),
    keyword: str | None = Query(
        default=None,
        max_length=200,
        description="项目名称搜索关键词",
    ),
    db: Session = Depends(get_db),
) -> ProjectListResponse:
    """
    分页查询血缘项目。
    """

    try:
        result = list_projects(
            db=db,
            page=page,
            page_size=page_size,
            keyword=keyword,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc),
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()

        logger.exception(
            "查询项目列表时数据库操作失败"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="查询项目列表失败",
        ) from exc

    return (
        ProjectListResponse
        .model_validate(result)
    )

# ============================================================
# 项目脚本列表
# ============================================================

@router.get(
    "/projects/{project_id}/scripts",
    response_model=ScriptListResponse,
    status_code=status.HTTP_200_OK,
    summary="查询项目脚本列表",
)
def list_project_scripts_endpoint(
    project_id: int,

    page: int = Query(
        default=1,
        ge=1,
        description="页码，从 1 开始",
    ),

    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="每页记录数",
    ),

    keyword: str | None = Query(
        default=None,
        max_length=200,
        description=(
            "文件名或相对路径搜索"
        ),
    ),

    parse_status: Literal[
        "pending",
        "success",
        "failed",
    ] | None = Query(
        default=None,
        description="解析状态过滤",
    ),

    db: Session = Depends(get_db),

) -> ScriptListResponse:
    """
    分页查询指定项目中的 SQL 脚本。
    """

    try:
        result = list_project_scripts(
            db=db,
            project_id=project_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            parse_status=parse_status,
        )

    except ValueError as exc:
        error_message = str(exc)

        if error_message.startswith(
            "血缘项目不存在"
        ):
            response_status = (
                status.HTTP_404_NOT_FOUND
            )

        else:
            response_status = (
                status.HTTP_400_BAD_REQUEST
            )

        raise HTTPException(
            status_code=response_status,
            detail=error_message,
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()

        logger.exception(
            "查询项目脚本列表时"
            "数据库操作失败"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="查询项目脚本列表失败",
        ) from exc

    return (
        ScriptListResponse
        .model_validate(result)
    )

# ============================================================
# 项目数据表列表
# ============================================================

@router.get(
    "/projects/{project_id}/tables",
    response_model=TableListResponse,
    status_code=status.HTTP_200_OK,
    summary="查询项目数据表列表",
)
def list_project_tables_endpoint(
    project_id: int,

    page: int = Query(
        default=1,
        ge=1,
        description="页码，从 1 开始",
    ),

    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="每页记录数",
    ),

    keyword: str | None = Query(
        default=None,
        max_length=300,
        description=(
            "catalog、schema 或表名关键词"
        ),
    ),

    table_kind: Literal[
        "physical",
        "view",
        "temp",
        "unknown",
    ] | None = Query(
        default=None,
        description="数据表类型过滤",
    ),

    db: Session = Depends(get_db),

) -> TableListResponse:
    """
    分页查询指定项目中的数据表。
    """

    try:
        result = list_project_tables(
            db=db,
            project_id=project_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            table_kind=table_kind,
        )

    except ValueError as exc:
        error_message = str(exc)

        if error_message.startswith(
            "血缘项目不存在"
        ):
            response_status = (
                status.HTTP_404_NOT_FOUND
            )

        else:
            response_status = (
                status.HTTP_400_BAD_REQUEST
            )

        raise HTTPException(
            status_code=response_status,
            detail=error_message,
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()

        logger.exception(
            "查询项目数据表列表时"
            "数据库操作失败"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="查询项目数据表列表失败",
        ) from exc

    return (
        TableListResponse
        .model_validate(result)
    )
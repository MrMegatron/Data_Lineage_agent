from __future__ import annotations

import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.schemas.ingestion import (
    SqlDirectoryIngestionRequest,
    SqlDirectoryIngestionResponse,
)
from app.services.sql_directory_ingestion_service import (
    ingest_sql_directory,
)
from app.services.sql_file_scanner import (
    SqlFileScanError,
)


logger = logging.getLogger(__name__)


# ============================================================
# 1. 创建路由
# ============================================================

router = APIRouter(
    prefix="/api/projects",
    tags=["SQL Ingestion"],
)


# ============================================================
# 2. 安全解析扫描目录
# ============================================================

def resolve_safe_sql_directory(
    relative_directory: str,
) -> Path:
    """
    将客户端传入的相对目录转换成安全绝对路径。

    只允许访问：

        settings.sql_source_root

    目录内部的路径。
    """

    root_directory = Path(
        settings.sql_source_root
    ).expanduser().resolve()

    requested_path = Path(
        relative_directory
    )

    # --------------------------------------------------------
    # 不允许客户端传入绝对路径
    # --------------------------------------------------------

    if requested_path.is_absolute():
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "relative_directory 必须是相对路径，"
                "不能传入绝对路径"
            ),
        )

    target_directory = (
        root_directory
        / requested_path
    ).resolve()

    # --------------------------------------------------------
    # 防止 ../ 越过根目录
    # --------------------------------------------------------

    try:
        target_directory.relative_to(
            root_directory
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "请求目录超出了允许扫描的"
                " SQL_SOURCE_ROOT"
            ),
        ) from exc

    return target_directory


# ============================================================
# 3. 批量导入 SQL 目录
# ============================================================

@router.post(
    "/{project_id}/ingest-directory",
    response_model=(
        SqlDirectoryIngestionResponse
    ),
    status_code=status.HTTP_200_OK,
    summary="扫描并导入 SQL 目录",
)
def ingest_directory_endpoint(
    project_id: int,
    request: SqlDirectoryIngestionRequest,
    db: Session = Depends(get_db),
) -> SqlDirectoryIngestionResponse:
    """
    扫描并导入一个项目的 SQL 目录。

    事务规则：

    1. 全部系统操作正常完成后 commit；
    2. 扫描或数据库操作异常时 rollback；
    3. SQL 语法失败会被记录，不属于系统异常。
    """

    target_directory = (
        resolve_safe_sql_directory(
            request.relative_directory
        )
    )

    try:
        dialect_override = (
            None
            if request.dialect == "auto"
            else request.dialect
        )

        result = ingest_sql_directory(
            db=db,
            project_id=project_id,
            root_directory=target_directory,
            dialect_override=dialect_override,
        )

        # 整个目录处理结束后只提交一次。
        db.commit()

    except SqlFileScanError as exc:
        db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        db.rollback()

        error_message = str(exc)

        if error_message.startswith(
            "血缘项目不存在"
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_404_NOT_FOUND
                ),
                detail=error_message,
            ) from exc

        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=error_message,
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()

        logger.exception(
            "批量导入 SQL 目录时数据库操作失败"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="数据库操作失败，导入事务已回滚",
        ) from exc

    except Exception as exc:
        db.rollback()

        logger.exception(
            "批量导入 SQL 目录时发生未知异常"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="导入失败，数据库事务已回滚",
        ) from exc

    return (
        SqlDirectoryIngestionResponse
        .model_validate(result)
    )
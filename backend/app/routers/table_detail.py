from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.table_detail import (
    TableDetailResponse,
)
from app.services.table_detail_service import (
    get_table_detail,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/projects",
    tags=["Table Detail"],
)


@router.get(
    "/{project_id}/tables/{table_id}",
    response_model=TableDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="查询数据表详情",
)
def get_table_detail_endpoint(
    project_id: int,
    table_id: int,
    db: Session = Depends(get_db),
) -> TableDetailResponse:
    """
    查询表字段、写入脚本、读取脚本和依赖。
    """

    try:
        result = get_table_detail(
            db=db,
            project_id=project_id,
            table_id=table_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=str(exc),
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()

        logger.exception(
            "查询数据表详情时数据库操作失败"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="查询数据表详情失败",
        ) from exc

    return (
        TableDetailResponse
        .model_validate(result)
    )
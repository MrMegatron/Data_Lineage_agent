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
from app.schemas.script_detail import (
    ScriptDetailResponse,
)
from app.services.script_detail_service import (
    get_script_detail,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/projects",
    tags=["Script Detail"],
)


@router.get(
    "/{project_id}/scripts/{script_id}",
    response_model=ScriptDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="查询脚本详情",
)
def get_script_detail_endpoint(
    project_id: int,
    script_id: int,
    db: Session = Depends(get_db),
) -> ScriptDetailResponse:
    """
    查询脚本源码、表访问、上游和下游。
    """

    try:
        result = get_script_detail(
            db=db,
            project_id=project_id,
            script_id=script_id,
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
            "查询脚本详情时数据库操作失败"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="查询脚本详情失败",
        ) from exc

    return (
        ScriptDetailResponse
        .model_validate(result)
    )
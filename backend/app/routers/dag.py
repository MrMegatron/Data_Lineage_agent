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
from app.schemas.dag import (
    ProjectScriptDagResponse,
)
from app.services.dag_query_service import (
    get_project_script_dag,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/projects",
    tags=["Script DAG"],
)


@router.get(
    "/{project_id}/dag",
    response_model=ProjectScriptDagResponse,
    status_code=status.HTTP_200_OK,
    summary="查询项目脚本 DAG",
)
def get_project_dag_endpoint(
    project_id: int,
    db: Session = Depends(get_db),
) -> ProjectScriptDagResponse:
    """
    返回项目的全部脚本节点和依赖边。
    """

    try:
        dag_result = get_project_script_dag(
            db=db,
            project_id=project_id,
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
            "查询项目脚本 DAG 时数据库操作失败"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="查询脚本 DAG 失败",
        ) from exc

    return (
        ProjectScriptDagResponse
        .model_validate(dag_result)
    )
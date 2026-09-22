from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    status,
)
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.column_search_schema import (
    ColumnSearchResponse,
)
from app.services.column_search_service import (
    search_project_columns,
)


# ============================================================
# 1. 创建Router
# ============================================================

router = APIRouter(
    prefix="/api/projects",
    tags=["Column Search"],
)


# ============================================================
# 2. 搜索项目字段
# ============================================================

@router.get(
    "/{project_id}/columns",
    response_model=ColumnSearchResponse,
    summary="搜索项目字段",
    description=(
        "在指定血缘项目中按照关键字、"
        "表名和字段名搜索数据字段。"
    ),
)
def search_columns(
    project_id: int = Path(
        ...,
        ge=1,
        description="血缘项目ID",
    ),
    page: int = Query(
        1,
        ge=1,
        description="页码，从1开始",
    ),
    page_size: int = Query(
        20,
        ge=1,
        le=100,
        description="每页记录数，最大100",
    ),
    keyword: str | None = Query(
        None,
        description=(
            "全局关键字，同时搜索"
            "表名、完整表名和字段名"
        ),
    ),
    table_name: str | None = Query(
        None,
        description=(
            "表名筛选，支持模糊匹配，"
            "例如 orders 或 dwd.orders"
        ),
    ),
    column_name: str | None = Query(
        None,
        description=(
            "字段名筛选，支持模糊匹配，"
            "例如 order_id"
        ),
    ),
    db: Session = Depends(get_db),
) -> ColumnSearchResponse:
    """
    搜索字段。

    正常：
        HTTP 200

    项目不存在：
        HTTP 404

    非法分页参数：
        HTTP 422
    """

    try:
        result = search_project_columns(
            db=db,
            project_id=project_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            table_name=table_name,
            column_name=column_name,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=str(exc),
        ) from exc

    return (
        ColumnSearchResponse
        .model_validate(result)
    )
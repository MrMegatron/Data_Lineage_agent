from typing import Literal
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
from app.schemas.column_lineage_schema import (
    ColumnLineageGraphResponse,
    ColumnLineageResponse,
)
from app.services.column_lineage_query_service import (
    get_column_lineage_detail,
    get_column_lineage_graph,
)


# ============================================================
# 1. 创建Router
# ============================================================

router = APIRouter(
    prefix="/api/columns",
    tags=["Column Lineage"],
)


# ============================================================
# 2. 查询字段直接上下游
# ============================================================

@router.get(
    "/{column_id}/lineage",
    response_model=ColumnLineageResponse,
    summary="查询字段直接上下游血缘",
    description=(
        "根据 column_id 查询当前字段、"
        "直接上游字段、直接下游字段、"
        "生成脚本和代码证据。"
    ),
)
def get_column_lineage(
    column_id: int = Path(
        ...,
        ge=1,
        description="数据字段ID",
    ),
    db: Session = Depends(get_db),
) -> ColumnLineageResponse:
    """
    查询一个字段的直接上下游血缘。

    正常：
        HTTP 200

    字段不存在：
        HTTP 404

    column_id 小于 1：
        HTTP 422
    """

    try:
        result = get_column_lineage_detail(
            db=db,
            column_id=column_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=str(exc),
        ) from exc

    return (
        ColumnLineageResponse
        .model_validate(result)
    )

# ============================================================
# 3. 查询字段多层血缘图
# ============================================================

@router.get(
    "/{column_id}/lineage/graph",
    response_model=(
        ColumnLineageGraphResponse
    ),
    summary="查询字段多层血缘图",
    description=(
        "根据 column_id 查询字段的多层上游、"
        "多层下游或者双向字段血缘。"
    ),
)
def get_column_lineage_graph_api(
    column_id: int = Path(
        ...,
        ge=1,
        description="根字段ID",
    ),
    direction: Literal[
        "upstream",
        "downstream",
        "both",
    ] = Query(
        "both",
        description=(
            "血缘遍历方向："
            "upstream、downstream 或 both"
        ),
    ),
    max_depth: int = Query(
        10,
        ge=1,
        le=20,
        description=(
            "最大遍历深度，范围1到20"
        ),
    ),
    db: Session = Depends(get_db),
) -> ColumnLineageGraphResponse:
    """
    查询字段多层血缘。

    示例：

        /api/columns/100/lineage/graph

        /api/columns/100/lineage/graph
            ?direction=upstream
            &max_depth=5

    返回：

        根字段
        所有字段节点
        所有字段血缘边
        每条边的深度
        SQL脚本
        代码证据
    """

    try:
        result = get_column_lineage_graph(
            db=db,
            column_id=column_id,
            direction=direction,
            max_depth=max_depth,
        )

    except ValueError as exc:
        # direction 和 max_depth 已经由 FastAPI
        # Query 参数完成校验。
        #
        # 能执行到这里的 ValueError，
        # 通常表示 column_id 不存在。
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=str(exc),
        ) from exc

    return (
        ColumnLineageGraphResponse
        .model_validate(result)
    )
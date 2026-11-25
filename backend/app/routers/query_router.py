from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, status

from app.core.containers import Container
from app.core.exceptions import AppException
from app.routers.deps import CurrentUser
from app.schemas.query_schema import QueryRequest
from app.services.query_service import QueryService

router = APIRouter(tags=["query"])


@router.post("/query")
@inject
def query(
    req: QueryRequest,
    _: CurrentUser,
    service: Annotated[QueryService, Depends(Provide[Container.query_service_provider])],
) -> dict:
    try:
        return service.answer(req.query, req.document_ids, req.top_k)
    except RuntimeError as exc:
        raise AppException(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR) from exc

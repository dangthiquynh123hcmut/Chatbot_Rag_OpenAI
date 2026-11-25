import uuid
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from app.core.containers import Container
from app.routers.deps import CurrentUser
from app.schemas.document_schema import SectionDetailResponse
from app.services.document_service import DocumentService

router = APIRouter(prefix="/sections", tags=["documents"])


@router.get("/{section_id}", response_model=SectionDetailResponse)
@inject
def get_section(
    section_id: uuid.UUID,
    _: CurrentUser,
    service: Annotated[DocumentService, Depends(Provide[Container.document_service_provider])],
):
    return service.get_section(section_id)

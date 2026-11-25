import uuid
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from app.core.containers import Container
from app.schemas.common_schema import DeletedResponse
from app.routers.deps import AdminUser, CurrentUser
from app.schemas.document_schema import (
    DocumentListResponse,
    DocumentResponse,
    MetadataUpdate,
    MetadataUpdateResponse,
    ReindexResponse,
    SectionTreeResponse,
    UploadResponse,
)
from app.services.document_service import DocumentService
from app.services.ingest_service import IngestService
from app.services.interfaces.i_document_service import UploadFileInput

router = APIRouter(prefix="/documents", tags=["documents"])

DocumentServiceDep = Annotated[DocumentService, Depends(Provide[Container.document_service_provider])]
IngestServiceDep = Annotated[IngestService, Depends(Provide[Container.ingest_service_provider])]


@router.post("", response_model=UploadResponse)
@inject
def upload_documents(
    background: BackgroundTasks,
    files: Annotated[list[UploadFile], File(...)],
    _: AdminUser,
    service: DocumentServiceDep,
    ingest_service: IngestServiceDep,
):
    result = service.upload([UploadFileInput(f.filename or "", f.file) for f in files])
    for job in result.jobs:
        background.add_task(ingest_service.process_document, job.document_id, job.path)
    return result.response


@router.get("", response_model=DocumentListResponse)
@inject
def list_documents(_: CurrentUser, service: DocumentServiceDep):
    return service.list_documents()


@router.get("/{document_id}", response_model=DocumentResponse)
@inject
def get_document(document_id: uuid.UUID, _: CurrentUser, service: DocumentServiceDep):
    return service.get_document(document_id)


@router.get("/{document_id}/markdown", response_class=PlainTextResponse)
@inject
def get_markdown(document_id: uuid.UUID, _: CurrentUser, service: DocumentServiceDep):
    return service.get_markdown(document_id)


@router.get("/{document_id}/tree", response_model=SectionTreeResponse)
@inject
def get_tree(document_id: uuid.UUID, _: CurrentUser, service: DocumentServiceDep):
    return service.get_tree(document_id)


@router.patch("/{document_id}/metadata", response_model=MetadataUpdateResponse)
@inject
def update_metadata(document_id: uuid.UUID, req: MetadataUpdate, _: AdminUser, service: DocumentServiceDep):
    return service.update_metadata(document_id, req)


@router.post("/{document_id}/reindex", response_model=ReindexResponse)
@inject
def reindex_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    _: AdminUser,
    service: DocumentServiceDep,
    ingest_service: IngestServiceDep,
):
    job = service.reindex(document_id)
    background.add_task(ingest_service.process_document, job.document_id, job.path)
    return ReindexResponse(id=str(document_id), status="processing")


@router.delete("/{document_id}", response_model=DeletedResponse)
@inject
def delete_document(document_id: uuid.UUID, _: AdminUser, service: DocumentServiceDep):
    service.delete_document(document_id)
    return DeletedResponse(deleted=str(document_id))


@router.get("/{document_id}/figures/{name}")
@inject
def get_figure(document_id: uuid.UUID, name: str, _: CurrentUser, service: DocumentServiceDep):
    return FileResponse(
        service.figure_path(document_id, name),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )

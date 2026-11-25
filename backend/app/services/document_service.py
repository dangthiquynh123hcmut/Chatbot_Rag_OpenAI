from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.converters import SUPPORTED_EXTENSIONS
from app.core.exceptions import BadRequestError, NotFoundError
from app.docling_pdf import figure_path, remove_figures
from app.metadata import DocMeta
from app.models.document_model import Document
from app.repositories.interfaces.i_document_repository import IDocumentRepository
from app.repositories.interfaces.i_section_repository import ISectionRepository
from app.schemas.document_schema import (
    DocumentListResponse,
    DocumentResponse,
    MetadataUpdate,
    MetadataUpdateResponse,
    SectionDetailResponse,
    SectionTreeResponse,
    UploadedDocument,
    UploadResponse,
)
from app.services.interfaces.i_document_service import IDocumentService, IngestJob, UploadFileInput, UploadResult
from app.utils.file_utils import markdown_path, stored_file, upload_path

COPY_BUFFER = 1024 * 1024


def default_title(filename: str) -> str:
    return Path(filename).stem.replace("_", " ").strip()


class DocumentService(IDocumentService):
    def __init__(self, session: Session, documents: IDocumentRepository, sections: ISectionRepository) -> None:
        self.session = session
        self.documents = documents
        self.sections = sections

    def upload(self, files: list[UploadFileInput]) -> UploadResult:
        for f in files:
            if Path(f.filename or "").suffix.lower() not in SUPPORTED_EXTENSIONS:
                raise BadRequestError(f"File '{f.filename}' không được hỗ trợ. Chỉ nhận PDF, DOCX.")
        result = UploadResult(response=UploadResponse(documents=[]))
        for f in files:
            document = self.documents.create(f.filename, default_title(f.filename))
            path = upload_path(document.id, f.filename)
            with open(path, "wb") as out:
                shutil.copyfileobj(f.file, out, COPY_BUFFER)
            result.response.documents.append(UploadedDocument(id=str(document.id), filename=f.filename))
            result.jobs.append(IngestJob(document.id, path))
        # The background ingestion reads the rows in its own session
        self.session.commit()
        return result

    def list_documents(self) -> DocumentListResponse:
        return DocumentListResponse(documents=[DocumentResponse.model_validate(d) for d in self.documents.list_all()])

    def get_document(self, document_id: uuid.UUID) -> DocumentResponse:
        return DocumentResponse.model_validate(self._document(document_id))

    def get_markdown(self, document_id: uuid.UUID) -> str:
        document = self.documents.get_with_markdown(document_id)
        if document is None:
            raise NotFoundError("Không tìm thấy tài liệu")
        return document.markdown or ""

    def get_tree(self, document_id: uuid.UUID) -> SectionTreeResponse:
        return SectionTreeResponse.model_validate({"sections": self.sections.tree(document_id)})

    def get_section(self, section_id: uuid.UUID) -> SectionDetailResponse:
        row = self.sections.detail(section_id)
        if row is None:
            raise NotFoundError("Không tìm thấy mục")
        return SectionDetailResponse.model_validate(row)

    def update_metadata(self, document_id: uuid.UUID, update: MetadataUpdate) -> MetadataUpdateResponse:
        document = self._document(document_id, lock=True)
        merged = {**DocMeta.from_dict(document.meta).to_dict(), **update.model_dump(exclude_unset=True), "source": "manual"}
        meta = DocMeta.from_dict(merged)
        document.meta = meta.to_dict()
        document.title = meta.title or document.title
        self.session.flush()
        return MetadataUpdateResponse(document=DocumentResponse.model_validate(document), reindex_recommended=True)

    def reindex(self, document_id: uuid.UUID) -> IngestJob:
        path = stored_file(document_id)
        if path is None:
            raise NotFoundError("Không còn file gốc để xử lý lại")
        document = self._document(document_id, lock=True)
        document.status, document.error = "processing", None
        self.session.commit()
        return IngestJob(document_id, path)

    def delete_document(self, document_id: uuid.UUID) -> None:
        self.documents.delete(self._document(document_id))
        self.session.commit()
        path = stored_file(document_id)
        if path:
            path.unlink(missing_ok=True)
        markdown_path(document_id).unlink(missing_ok=True)
        remove_figures(str(document_id))

    def figure_path(self, document_id: uuid.UUID, name: str) -> Path:
        path = figure_path(str(document_id), name)
        if path is None:
            raise NotFoundError("Không tìm thấy hình")
        return path

    def _document(self, document_id: uuid.UUID, lock: bool = False) -> Document:
        document = self.documents.get(document_id, lock=lock)
        if document is None:
            raise NotFoundError("Không tìm thấy tài liệu")
        return document

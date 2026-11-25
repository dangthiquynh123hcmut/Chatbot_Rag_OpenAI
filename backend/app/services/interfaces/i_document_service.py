from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from app.schemas.document_schema import (
    DocumentListResponse,
    DocumentResponse,
    MetadataUpdate,
    MetadataUpdateResponse,
    SectionDetailResponse,
    SectionTreeResponse,
    UploadResponse,
)


@dataclass
class UploadFileInput:
    filename: str
    file: BinaryIO


@dataclass
class IngestJob:
    """A stored file to (re)ingest in the background once the documents are committed."""

    document_id: uuid.UUID
    path: Path


@dataclass
class UploadResult:
    response: UploadResponse
    jobs: list[IngestJob] = field(default_factory=list)


class IDocumentService(ABC):
    @abstractmethod
    def upload(self, files: list[UploadFileInput]) -> UploadResult:
        """Store the files as documents in status 'processing' (committed); raises BadRequestError
        if any file type is not supported, before storing anything."""

    @abstractmethod
    def list_documents(self) -> DocumentListResponse:
        pass

    @abstractmethod
    def get_document(self, document_id: uuid.UUID) -> DocumentResponse:
        pass

    @abstractmethod
    def get_markdown(self, document_id: uuid.UUID) -> str:
        pass

    @abstractmethod
    def get_tree(self, document_id: uuid.UUID) -> SectionTreeResponse:
        pass

    @abstractmethod
    def get_section(self, section_id: uuid.UUID) -> SectionDetailResponse:
        pass

    @abstractmethod
    def update_metadata(self, document_id: uuid.UUID, update: MetadataUpdate) -> MetadataUpdateResponse:
        """Manual metadata (source='manual'), kept by later ingestions."""

    @abstractmethod
    def reindex(self, document_id: uuid.UUID) -> IngestJob:
        """Set the document back to 'processing' (committed); the job ingests its stored file again."""

    @abstractmethod
    def delete_document(self, document_id: uuid.UUID) -> None:
        """The document, its index, stored file, markdown and figures."""

    @abstractmethod
    def figure_path(self, document_id: uuid.UUID, name: str) -> Path:
        pass

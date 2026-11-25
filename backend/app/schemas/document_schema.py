from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MetadataUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    short_title: str | None = Field(default=None, max_length=200)
    authors: list[str] | None = Field(default=None, max_length=50)
    year: int | None = Field(default=None, ge=1400, le=2100)
    doc_type: str | None = None
    publisher: str | None = Field(default=None, max_length=300)
    isbn: str | None = Field(default=None, max_length=30)
    aliases: list[str] | None = Field(default=None, max_length=20)
    summary: str | None = Field(default=None, max_length=1000)


class DocumentResponse(BaseModel):
    """A document without its markdown (GET /documents/{id}/markdown)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    title: str
    status: str
    error: str | None = None
    num_sections: int | None = None
    num_chunks: int | None = None
    created_at: datetime
    meta: dict


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]


class UploadedDocument(BaseModel):
    id: str
    filename: str
    status: str = "processing"


class UploadResponse(BaseModel):
    documents: list[UploadedDocument]


class MetadataUpdateResponse(BaseModel):
    document: DocumentResponse
    reindex_recommended: bool = True


class ReindexResponse(BaseModel):
    id: str
    status: str


class SectionNode(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None = None
    depth: int
    kind: str
    name: str | None = None
    title: str | None = None
    refs: dict


class SectionTreeResponse(BaseModel):
    sections: list[SectionNode]


class SectionDetailResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    kind: str
    title: str | None = None
    path: list[str]
    refs: dict
    full_md: str | None = None

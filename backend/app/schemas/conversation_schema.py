from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

TITLE_MAX = 80


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)


class MessageCreate(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    document_ids: list[uuid.UUID] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationSummary(ConversationResponse):
    message_count: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    result: dict | None = None
    document_ids: list[uuid.UUID] | None = None
    created_at: datetime


class ConversationDetailResponse(ConversationResponse):
    messages: list[MessageResponse]


class SendMessageResponse(BaseModel):
    conversation: ConversationResponse
    user_message: MessageResponse
    assistant_message: MessageResponse

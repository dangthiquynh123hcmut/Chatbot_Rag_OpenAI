from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.schemas.conversation_schema import (
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    MessageCreate,
    SendMessageResponse,
)


class IConversationService(ABC):
    @abstractmethod
    def list_conversations(self, user_id: uuid.UUID) -> ConversationListResponse:
        pass

    @abstractmethod
    def create_conversation(self, user_id: uuid.UUID, title: str | None) -> ConversationResponse:
        pass

    @abstractmethod
    def get_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> ConversationDetailResponse:
        """Raises NotFoundError when it does not exist or belongs to another user."""

    @abstractmethod
    def rename_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID, title: str) -> ConversationResponse:
        pass

    @abstractmethod
    def delete_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        pass

    @abstractmethod
    def send_message(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, request: MessageCreate
    ) -> SendMessageResponse:
        """Store the question, answer it with the RAG pipeline and store the answer. The question is
        committed before the (slow) answer, so no transaction stays open meanwhile."""

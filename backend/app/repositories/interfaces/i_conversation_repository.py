from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.models.conversation_model import Conversation, Message


class IConversationRepository(ABC):
    @abstractmethod
    def list_with_message_counts(self, user_id: uuid.UUID) -> list[tuple[Conversation, int]]:
        """The user's conversations, most recently updated first, with their number of messages."""

    @abstractmethod
    def create(self, user_id: uuid.UUID, title: str) -> Conversation:
        pass

    @abstractmethod
    def get_owned(self, conversation_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False) -> Conversation | None:
        """The conversation if it belongs to the user (row locked if `lock`)."""

    @abstractmethod
    def delete(self, conversation: Conversation) -> None:
        pass

    @abstractmethod
    def add_message(
        self,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        result: dict | None = None,
        document_ids: list[uuid.UUID] | None = None,
    ) -> Message:
        pass

    @abstractmethod
    def touch(self, conversation: Conversation) -> None:
        """Set updated_at to now."""

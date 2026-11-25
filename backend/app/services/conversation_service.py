from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.conversation_model import Conversation
from app.repositories.interfaces.i_conversation_repository import IConversationRepository
from app.schemas.conversation_schema import (
    TITLE_MAX,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationSummary,
    MessageCreate,
    MessageResponse,
    SendMessageResponse,
)
from app.services.interfaces.i_conversation_service import IConversationService
from app.services.interfaces.i_query_service import IQueryService

log = logging.getLogger(__name__)

DEFAULT_TITLE = "Cuộc hội thoại mới"


def make_title(query: str) -> str:
    text = " ".join(query.split())
    return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 1].rstrip() + "…"


class ConversationService(IConversationService):
    def __init__(self, session: Session, repository: IConversationRepository, query_service: IQueryService) -> None:
        self.session = session
        self.repository = repository
        self.query_service = query_service

    def list_conversations(self, user_id: uuid.UUID) -> ConversationListResponse:
        return ConversationListResponse(
            conversations=[
                ConversationSummary(
                    id=conversation.id,
                    title=conversation.title,
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                    message_count=count,
                )
                for conversation, count in self.repository.list_with_message_counts(user_id)
            ]
        )

    def create_conversation(self, user_id: uuid.UUID, title: str | None) -> ConversationResponse:
        title = make_title(title) if title and title.strip() else DEFAULT_TITLE
        return ConversationResponse.model_validate(self.repository.create(user_id, title))

    def get_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> ConversationDetailResponse:
        return ConversationDetailResponse.model_validate(self._owned(conversation_id, user_id))

    def rename_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID, title: str) -> ConversationResponse:
        conversation = self._owned(conversation_id, user_id, lock=True)
        conversation.title = make_title(title)
        self.session.flush()
        return ConversationResponse.model_validate(conversation)

    def delete_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.repository.delete(self._owned(conversation_id, user_id))

    def send_message(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, request: MessageCreate
    ) -> SendMessageResponse:
        conversation = self._owned(conversation_id, user_id, lock=True)
        user_message = self.repository.add_message(
            conversation_id, "user", request.query.strip(), document_ids=request.document_ids
        )
        if conversation.title == DEFAULT_TITLE:
            conversation.title = make_title(request.query)
        self.session.flush()
        user_message_response = MessageResponse.model_validate(user_message)
        # The question is saved and the row lock released before the slow LLM calls
        self.session.commit()

        try:
            result = self.query_service.answer(request.query, request.document_ids, request.top_k)
            content = result.get("answer") or ""
        except Exception as exc:
            log.exception("Answer failed in conversation %s", conversation_id)
            result = {"error": f"Lỗi khi trả lời: {exc}"}
            content = result["error"]

        # Deleted meanwhile: 404
        conversation = self._owned(conversation_id, user_id, lock=True)
        assistant_message = self.repository.add_message(conversation_id, "assistant", content, result=result)
        self.repository.touch(conversation)
        return SendMessageResponse(
            conversation=ConversationResponse.model_validate(conversation),
            user_message=user_message_response,
            assistant_message=MessageResponse.model_validate(assistant_message),
        )

    def _owned(self, conversation_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False) -> Conversation:
        conversation = self.repository.get_owned(conversation_id, user_id, lock=lock)
        if conversation is None:
            raise NotFoundError("Không tìm thấy cuộc hội thoại")
        return conversation

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.conversation_model import Conversation, Message
from app.repositories.interfaces.i_conversation_repository import IConversationRepository


class ConversationRepository(IConversationRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_with_message_counts(self, user_id: uuid.UUID) -> list[tuple[Conversation, int]]:
        message_count = func.count(Message.id).label("message_count")
        rows = self.session.execute(
            select(Conversation, message_count)
            .outerjoin(Message, Message.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id)
            .group_by(Conversation.id)
            .order_by(Conversation.updated_at.desc())
        ).all()
        return [(conversation, count) for conversation, count in rows]

    def create(self, user_id: uuid.UUID, title: str) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def get_owned(self, conversation_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False) -> Conversation | None:
        stmt = select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        if lock:
            # Locked to be changed: reload what another transaction may have changed meanwhile
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(stmt)

    def delete(self, conversation: Conversation) -> None:
        # Messages go with it (ON DELETE CASCADE)
        self.session.delete(conversation)
        self.session.flush()

    def add_message(
        self,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        result: dict | None = None,
        document_ids: list[uuid.UUID] | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id, role=role, content=content, result=result, document_ids=document_ids
        )
        self.session.add(message)
        self.session.flush()
        return message

    def touch(self, conversation: Conversation) -> None:
        conversation.updated_at = func.now()
        self.session.flush()

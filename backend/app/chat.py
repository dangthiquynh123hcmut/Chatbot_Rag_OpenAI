from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import get_current_user
from .db import get_session, session_scope
from .models import Conversation, Message
from .rag import answer_query

log = logging.getLogger(__name__)

DEFAULT_TITLE = "Cuộc hội thoại mới"
TITLE_MAX = 80

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)


class MessageCreate(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    document_ids: list[uuid.UUID] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


def make_title(query: str) -> str:
    text = " ".join(query.split())
    return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 1].rstrip() + "…"


def _owned(db: Session, conversation_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False) -> Conversation:
    stmt = select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    conv = db.scalar(stmt.with_for_update() if lock else stmt)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy cuộc hội thoại")
    return conv


@router.get("")
def list_conversations(user: dict = Depends(get_current_user), db: Session = Depends(get_session)):
    message_count = func.count(Message.id).label("message_count")
    rows = db.execute(
        select(Conversation, message_count)
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user["id"])
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc())
    ).all()
    return {"conversations": [{**conv.to_dict(), "message_count": count} for conv, count in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_conversation(
    req: ConversationCreate, user: dict = Depends(get_current_user), db: Session = Depends(get_session)
):
    title = make_title(req.title) if req.title and req.title.strip() else DEFAULT_TITLE
    conv = Conversation(user_id=user["id"], title=title)
    db.add(conv)
    db.flush()
    return conv.to_dict()


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: uuid.UUID, user: dict = Depends(get_current_user), db: Session = Depends(get_session)
):
    conv = _owned(db, conversation_id, user["id"])
    return {**conv.to_dict(), "messages": [m.to_dict() for m in conv.messages]}


@router.patch("/{conversation_id}")
def rename_conversation(
    conversation_id: uuid.UUID,
    req: ConversationRename,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    conv = _owned(db, conversation_id, user["id"], lock=True)
    conv.title = make_title(req.title)
    db.flush()
    return conv.to_dict()


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: uuid.UUID, user: dict = Depends(get_current_user), db: Session = Depends(get_session)
):
    db.delete(_owned(db, conversation_id, user["id"]))
    return {"deleted": str(conversation_id)}


@router.post("/{conversation_id}/messages")
def send_message(conversation_id: uuid.UUID, req: MessageCreate, user: dict = Depends(get_current_user)):
    with session_scope() as db:
        conv = _owned(db, conversation_id, user["id"], lock=True)
        user_msg = Message(
            conversation_id=conversation_id, role="user", content=req.query.strip(), document_ids=req.document_ids
        )
        db.add(user_msg)
        if conv.title == DEFAULT_TITLE:
            conv.title = make_title(req.query)
        db.flush()
        user_msg_dict = user_msg.to_dict()

    try:
        result = answer_query(req.query, req.document_ids, req.top_k)
        content = result.get("answer") or ""
    except Exception as exc:
        log.exception("Answer failed in conversation %s", conversation_id)
        result = {"error": f"Lỗi khi trả lời: {exc}"}
        content = result["error"]

    with session_scope() as db:
        conv = _owned(db, conversation_id, user["id"], lock=True)
        assistant_msg = Message(conversation_id=conversation_id, role="assistant", content=content, result=result)
        db.add(assistant_msg)
        conv.updated_at = func.now()
        db.flush()
        return {
            "conversation": conv.to_dict(),
            "user_message": user_msg_dict,
            "assistant_message": assistant_msg.to_dict(),
        }

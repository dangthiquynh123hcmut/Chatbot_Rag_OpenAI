from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import get_current_user
from .db import connection
from .rag import answer_query

log = logging.getLogger(__name__)

DEFAULT_TITLE = "Cuộc hội thoại mới"
TITLE_MAX = 80
CONV_COLS = "c.id, c.title, c.created_at, c.updated_at"
MSG_COLS = "id, role, content, result, document_ids, created_at"

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


def _owned(conn, conversation_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    row = conn.execute(
        f"SELECT {CONV_COLS} FROM conversations c WHERE c.id=%s AND c.user_id=%s", (conversation_id, user_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy cuộc hội thoại")
    return row


@router.get("")
def list_conversations(user: dict = Depends(get_current_user)):
    with connection() as conn:
        rows = conn.execute(
            f"""SELECT {CONV_COLS}, count(m.id) AS message_count
                FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.user_id=%s GROUP BY c.id ORDER BY c.updated_at DESC""",
            (user["id"],),
        ).fetchall()
    return {"conversations": rows}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_conversation(req: ConversationCreate, user: dict = Depends(get_current_user)):
    title = make_title(req.title) if req.title and req.title.strip() else DEFAULT_TITLE
    with connection() as conn:
        return conn.execute(
            """INSERT INTO conversations (id, user_id, title) VALUES (%s, %s, %s)
               RETURNING id, title, created_at, updated_at""",
            (uuid.uuid4(), user["id"], title),
        ).fetchone()


@router.get("/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID, user: dict = Depends(get_current_user)):
    with connection() as conn:
        conv = _owned(conn, conversation_id, user["id"])
        conv["messages"] = conn.execute(
            f"SELECT {MSG_COLS} FROM messages WHERE conversation_id=%s ORDER BY created_at", (conversation_id,)
        ).fetchall()
    return conv


@router.patch("/{conversation_id}")
def rename_conversation(conversation_id: uuid.UUID, req: ConversationRename, user: dict = Depends(get_current_user)):
    with connection() as conn:
        _owned(conn, conversation_id, user["id"])
        return conn.execute(
            "UPDATE conversations SET title=%s WHERE id=%s RETURNING id, title, created_at, updated_at",
            (make_title(req.title), conversation_id),
        ).fetchone()


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: uuid.UUID, user: dict = Depends(get_current_user)):
    with connection() as conn:
        _owned(conn, conversation_id, user["id"])
        conn.execute("DELETE FROM conversations WHERE id=%s", (conversation_id,))
    return {"deleted": str(conversation_id)}


@router.post("/{conversation_id}/messages")
def send_message(conversation_id: uuid.UUID, req: MessageCreate, user: dict = Depends(get_current_user)):
    with connection() as conn:
        conv = _owned(conn, conversation_id, user["id"])
        user_msg = conn.execute(
            f"""INSERT INTO messages (id, conversation_id, role, content, document_ids)
                VALUES (%s, %s, 'user', %s, %s) RETURNING {MSG_COLS}""",
            (uuid.uuid4(), conversation_id, req.query.strip(), req.document_ids),
        ).fetchone()
        if conv["title"] == DEFAULT_TITLE:
            conn.execute("UPDATE conversations SET title=%s WHERE id=%s", (make_title(req.query), conversation_id))

    try:
        result = answer_query(req.query, req.document_ids, req.top_k)
        content = result.get("answer") or ""
    except Exception as exc:
        log.exception("Answer failed in conversation %s", conversation_id)
        result = {"error": f"Lỗi khi trả lời: {exc}"}
        content = result["error"]

    with connection() as conn:
        assistant_msg = conn.execute(
            f"""INSERT INTO messages (id, conversation_id, role, content, result)
                VALUES (%s, %s, 'assistant', %s, %s) RETURNING {MSG_COLS}""",
            (uuid.uuid4(), conversation_id, content, Jsonb(result)),
        ).fetchone()
        conv = conn.execute(
            "UPDATE conversations SET updated_at=now() WHERE id=%s RETURNING id, title, created_at, updated_at",
            (conversation_id,),
        ).fetchone()
    return {"conversation": conv, "user_message": user_msg, "assistant_message": assistant_msg}

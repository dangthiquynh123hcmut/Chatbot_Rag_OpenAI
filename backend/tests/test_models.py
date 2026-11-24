import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config import get_settings
from app.models import CRUD_TABLES, RAW_SQL_TABLES, Base, Chunk, Conversation, Document, Message, User


def _ddl(obj) -> str:
    return str(obj.compile(dialect=postgresql.dialect()))


def test_all_tables_are_modelled():
    assert set(Base.metadata.tables) == set(CRUD_TABLES) | set(RAW_SQL_TABLES)
    assert set(Base.metadata.tables) == {
        "documents", "sections", "chunks", "users", "auth_sessions", "refresh_tokens", "conversations", "messages",
    }


def test_chunk_columns_match_postgres_features():
    ddl = _ddl(CreateTable(Chunk.__table__))
    assert f"VECTOR({get_settings().embedding_dim})" in ddl
    assert "GENERATED ALWAYS AS (to_tsvector('english'::regconfig, embed_text)) STORED" in ddl
    indexes = {i.name: _ddl(CreateIndex(i)) for i in Chunk.__table__.indexes}
    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["chunks_embedding_idx"]
    assert "USING gin (tsv)" in indexes["chunks_tsv_idx"]


def test_constraints_and_defaults():
    users = _ddl(CreateTable(User.__table__))
    assert "CONSTRAINT users_username_key UNIQUE (username)" in users
    assert "CHECK (role IN ('user', 'admin'))" in users
    messages = _ddl(CreateTable(Message.__table__))
    assert "DEFAULT clock_timestamp()" in messages and "ON DELETE CASCADE" in messages
    assert "meta JSONB DEFAULT '{}'::jsonb NOT NULL" in _ddl(CreateTable(Document.__table__))


def test_to_dict_shapes():
    now = datetime.now(timezone.utc)
    u = User(id=uuid.uuid4(), username="a", password_hash="secret", display_name="A", role="user",
             created_at=now, last_login_at=None)
    assert set(u.to_dict()) == {"id", "username", "display_name", "role", "created_at", "last_login_at"}
    assert "password_hash" not in u.to_dict()
    c = Conversation(id=uuid.uuid4(), user_id=u.id, title="t", created_at=now, updated_at=now)
    assert set(c.to_dict()) == {"id", "title", "created_at", "updated_at"}
    m = Message(id=uuid.uuid4(), conversation_id=c.id, role="user", content="q", created_at=now)
    assert set(m.to_dict()) == {"id", "role", "content", "result", "document_ids", "created_at"}
    d = Document(id=uuid.uuid4(), filename="f.pdf", title="T", status="ready", meta={})
    assert "markdown" not in d.to_dict() and d.to_dict()["meta"] == {}

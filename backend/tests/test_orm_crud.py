import os
import uuid

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import chat, config, db, main

    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    config.get_settings.cache_clear()
    db.close_pool()
    monkeypatch.setattr(chat, "answer_query", lambda q, d, k: {"answer": f"echo: {q}", "found": True, "sources": []})
    with TestClient(main.app) as c:
        yield c
    db.close_pool()
    config.get_settings.cache_clear()


def _login(client, role="user"):
    from app.auth import create_user

    name = f"o{uuid.uuid4().hex[:10]}"
    create_user(name, "correct-horse-1", role=role)
    r = client.post("/api/auth/login", json={"username": name, "password": "correct-horse-1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_conversation_lifecycle(client):
    alice, bob = _login(client), _login(client)
    conv = client.post("/api/conversations", json={}, headers=alice).json()
    assert conv["title"] == "Cuộc hội thoại mới" and conv["created_at"] and conv["updated_at"]

    r = client.post(f"/api/conversations/{conv['id']}/messages", json={"query": "  What is RAG?  "}, headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_message"]["content"] == "What is RAG?" and body["user_message"]["created_at"]
    assert body["assistant_message"]["result"]["answer"].strip() == "echo:   What is RAG?".strip()
    assert body["assistant_message"]["content"] == body["assistant_message"]["result"]["answer"]
    assert body["conversation"]["title"] == "What is RAG?"
    assert body["conversation"]["updated_at"] > conv["updated_at"]

    client.post(f"/api/conversations/{conv['id']}/messages", json={"query": "Second?"}, headers=alice)
    detail = client.get(f"/api/conversations/{conv['id']}", headers=alice).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["title"] == "What is RAG?"

    listed = client.get("/api/conversations", headers=alice).json()["conversations"]
    assert listed[0]["id"] == conv["id"] and listed[0]["message_count"] == 4

    assert client.get(f"/api/conversations/{conv['id']}", headers=bob).status_code == 404
    assert client.post(f"/api/conversations/{conv['id']}/messages", json={"query": "hi there"}, headers=bob).status_code == 404
    assert client.get("/api/conversations", headers=bob).json()["conversations"] == []

    renamed = client.patch(f"/api/conversations/{conv['id']}", json={"title": "Renamed"}, headers=alice).json()
    assert renamed["title"] == "Renamed"
    assert client.delete(f"/api/conversations/{conv['id']}", headers=alice).json() == {"deleted": conv["id"]}
    assert client.get(f"/api/conversations/{conv['id']}", headers=alice).status_code == 404


def test_document_crud(client):
    from app.db import session_scope
    from app.models import Document

    user, admin = _login(client), _login(client, role="admin")
    doc_id = uuid.uuid4()
    with session_scope() as s:
        s.add(Document(id=doc_id, filename="paper.pdf", title="paper", status="ready",
                       markdown="# Paper", meta={"title": "paper"}))

    docs = client.get("/api/documents", headers=user).json()["documents"]
    mine = next(d for d in docs if d["id"] == str(doc_id))
    assert mine["status"] == "ready" and mine["num_chunks"] == 0 and "markdown" not in mine
    assert client.get(f"/api/documents/{doc_id}/markdown", headers=user).text == "# Paper"
    assert client.get(f"/api/documents/{uuid.uuid4()}", headers=user).status_code == 404

    assert client.patch(f"/api/documents/{doc_id}/metadata", json={"year": 2020}, headers=user).status_code == 403
    r = client.patch(f"/api/documents/{doc_id}/metadata",
                     json={"title": "A Better Title", "authors": ["Ada Lovelace"], "year": 2020}, headers=admin)
    assert r.status_code == 200, r.text
    updated = r.json()["document"]
    assert updated["title"] == "A Better Title" and updated["meta"]["source"] == "manual"
    assert client.get(f"/api/documents/{doc_id}", headers=user).json()["meta"]["authors"] == ["Ada Lovelace"]

    assert client.delete(f"/api/documents/{doc_id}", headers=user).status_code == 403
    assert client.delete(f"/api/documents/{doc_id}", headers=admin).json() == {"deleted": str(doc_id)}
    assert client.get(f"/api/documents/{doc_id}", headers=user).status_code == 404

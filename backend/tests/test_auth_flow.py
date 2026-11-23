"""End-to-end auth flow against a real PostgreSQL (runs migrations on a throwaway DB).

Skipped unless TEST_DATABASE_URL points at a disposable database, e.g.
    TEST_DATABASE_URL=postgresql://rag:rag@localhost:55432/rag python -m pytest tests/test_auth_flow.py
"""
import os
import uuid

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth, config, db, main

    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("REFRESH_REUSE_GRACE_SECONDS", "0")
    config.get_settings.cache_clear()
    db.close_pool()
    with TestClient(main.app) as c:
        yield c
    db.close_pool()
    config.get_settings.cache_clear()


def _register(client, password="correct-horse-1"):
    name = f"u{uuid.uuid4().hex[:10]}"
    r = client.post("/api/auth/register", json={"username": name, "password": password})
    if r.status_code == 403:  # registration closed once users exist
        pytest.skip("ALLOW_REGISTRATION=false in this environment")
    assert r.status_code == 201, r.text
    return name, r.json()


def _use_refresh_cookie(client, value):
    client.cookies.clear()
    client.cookies.set("rag_refresh", value, path="/api/auth")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_login_sets_httponly_refresh_cookie(client):
    _, body = _register(client)
    cookie = client.cookies.jar
    refresh = next(c for c in cookie if c.name == "rag_refresh")
    assert refresh.path == "/api/auth" and refresh.has_nonstandard_attr("HttpOnly")
    assert client.get("/api/auth/me", headers=_bearer(body["access_token"])).status_code == 200


def test_refresh_rotates_and_detects_reuse(client):
    _, body = _register(client)
    first_refresh = client.cookies.get("rag_refresh")
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    second_refresh = client.cookies.get("rag_refresh")
    assert second_refresh and second_refresh != first_refresh
    new_access = r.json()["access_token"]
    assert client.get("/api/auth/me", headers=_bearer(new_access)).status_code == 200

    # Replaying the old (already used) refresh token = theft: the whole session is revoked.
    _use_refresh_cookie(client, first_refresh)
    assert client.post("/api/auth/refresh").status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(new_access)).status_code == 401
    _use_refresh_cookie(client, second_refresh)
    assert client.post("/api/auth/refresh").status_code == 401


def test_logout_revokes_access_and_refresh(client):
    _, body = _register(client)
    access = body["access_token"]
    assert client.post("/api/auth/logout", headers=_bearer(access)).status_code == 204
    assert client.get("/api/auth/me", headers=_bearer(access)).status_code == 401
    assert client.post("/api/auth/refresh").status_code == 401


def test_change_password_revokes_other_sessions(client):
    from fastapi.testclient import TestClient

    from app import main

    name, body = _register(client)
    with TestClient(main.app) as other_device:
        r = other_device.post("/api/auth/login", json={"username": name, "password": "correct-horse-1"})
        other_access = r.json()["access_token"]
        r = client.post(
            "/api/auth/change-password",
            json={"current_password": "correct-horse-1", "new_password": "new-horse-battery-2"},
            headers=_bearer(body["access_token"]),
        )
        assert r.status_code == 200 and r.json()["revoked_sessions"] == 1
        assert other_device.get("/api/auth/me", headers=_bearer(other_access)).status_code == 401
        assert other_device.post("/api/auth/refresh").status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(body["access_token"])).status_code == 200
    bad = client.post("/api/auth/login", json={"username": name, "password": "correct-horse-1"})
    assert bad.status_code == 401
    assert client.post("/api/auth/login", json={"username": name, "password": "new-horse-battery-2"}).status_code == 200


def test_logout_all_and_wrong_current_password(client):
    _, body = _register(client)
    access = body["access_token"]
    r = client.post("/api/auth/change-password", json={"current_password": "nope", "new_password": "whatever-123"},
                    headers=_bearer(access))
    assert r.status_code == 400
    assert client.post("/api/auth/logout-all", headers=_bearer(access)).status_code == 204
    assert client.get("/api/auth/me", headers=_bearer(access)).status_code == 401

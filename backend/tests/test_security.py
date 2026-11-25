from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import llm
from app.config import Settings, get_settings
from app.core import security
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.services.auth_service import UserError, normalize_username
from app.services.conversation_service import make_title


def test_password_hash_roundtrip():
    h = hash_password("mật-khẩu-đủ-dài")
    assert h.startswith("scrypt$") and "mật" not in h
    assert verify_password("mật-khẩu-đủ-dài", h)
    assert not verify_password("sai-mat-khau", h)
    assert hash_password("x" * 10) != hash_password("x" * 10)
    assert not verify_password("anything", "garbage")
    assert not verify_password("anything", "bcrypt$1$2$3$4$5")


def test_token_roundtrip_and_expiry():
    token, expires = create_access_token("6f1c0b5e-0000-4000-8000-000000000001", "admin", "sid-1")
    payload = decode_access_token(token)
    assert payload["sub"].endswith("0001") and payload["role"] == "admin"
    assert payload["sid"] == "sid-1" and payload["typ"] == "access" and payload["jti"]
    assert expires > datetime.now(timezone.utc)
    assert expires - datetime.now(timezone.utc) <= timedelta(minutes=get_settings().jwt_expire_minutes)
    old, _ = create_access_token("u", "user", "s", now=datetime.now(timezone.utc) - timedelta(days=30))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(old)
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(jwt.encode({"sub": "u", "iat": 1, "exp": 9999999999}, "khoa-khac-dai-hon-32-byte-cho-hs256"))


def test_token_requires_session_and_type():
    secret = security._secret()
    legacy = jwt.encode({"sub": "u", "role": "user", "iat": 1, "exp": 9999999999}, secret, algorithm="HS256")
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(legacy)
    other = jwt.encode({"sub": "u", "sid": "s", "typ": "refresh", "iat": 1, "exp": 9999999999}, secret, algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(other)


def test_refresh_token_is_hashed():
    token, digest = new_refresh_token()
    assert len(token) >= 40 and token not in digest
    assert hash_refresh_token(token) == digest and len(digest) == 64


def test_jwt_secret_required_outside_development():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret="")
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret="too-short")
    assert Settings(app_env="production", jwt_secret="x" * 32).jwt_secret
    assert Settings(app_env="development", jwt_secret="").is_development


def test_username_rules():
    assert normalize_username("  Admin.User ") == "admin.user"
    for bad in ["ab", "tên có dấu", "a" * 41, "x y"]:
        with pytest.raises(UserError):
            normalize_username(bad)


def test_make_title():
    assert make_title("  Điều 3   quy định gì? ") == "Điều 3 quy định gì?"
    long = make_title("a " * 100)
    assert len(long) == 80 and long.endswith("…")


def test_network_retry(monkeypatch):
    class EndpointConnectionError(Exception):
        pass

    sleeps, calls = [], []
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise EndpointConnectionError("dns")
        return "ok"

    assert llm.with_network_retry(flaky) == "ok"
    assert sleeps == [2, 4]

    def permanent():
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        llm.with_network_retry(permanent)
    assert sleeps == [2, 4]

    calls.clear()

    def always_down():
        calls.append(1)
        raise EndpointConnectionError("dns")

    with pytest.raises(EndpointConnectionError):
        llm.with_network_retry(always_down)
    assert len(calls) == len(llm.NETWORK_BACKOFF_S) + 1

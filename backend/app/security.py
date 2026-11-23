from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

import jwt

from .config import get_settings

log = logging.getLogger(__name__)

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN = 2**14, 8, 1, 32
JWT_ALGORITHM = "HS256"
_secret_lock = threading.Lock()
_generated_secret: str | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _secret() -> str:
    global _generated_secret
    configured = get_settings().jwt_secret
    if configured:
        return configured
    with _secret_lock:
        if _generated_secret is None:
            _generated_secret = secrets.token_urlsafe(48)
            log.warning("JWT_SECRET chưa được đặt – dùng khóa ngẫu nhiên, mọi phiên đăng nhập mất hiệu lực khi khởi động lại")
    return _generated_secret


def create_access_token(user_id: str, role: str, now: datetime | None = None) -> tuple[str, datetime]:
    issued = now or datetime.now(timezone.utc)
    expires = issued + timedelta(minutes=get_settings().jwt_expire_minutes)
    payload = {"sub": user_id, "role": role, "iat": issued, "exp": expires}
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM), expires


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM], options={"require": ["sub", "exp", "iat"]})

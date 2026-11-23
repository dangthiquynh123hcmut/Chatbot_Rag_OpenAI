from __future__ import annotations

import re
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .config import get_settings
from .db import connection
from .security import create_access_token, decode_access_token, hash_password, verify_password

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")
USER_COLS = "id, username, display_name, role, created_at, last_login_at"

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class RegisterRequest(Credentials):
    display_name: str | None = Field(default=None, max_length=80)


class UserError(ValueError):
    pass


def normalize_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME_RE.match(name):
        raise UserError("Tên đăng nhập 3-40 ký tự, chỉ gồm chữ thường không dấu, số, dấu chấm, gạch dưới, gạch ngang.")
    return name


def create_user(username: str, password: str, display_name: str | None = None, role: str | None = None) -> dict:
    settings = get_settings()
    name = normalize_username(username)
    if len(password) < settings.min_password_length:
        raise UserError(f"Mật khẩu tối thiểu {settings.min_password_length} ký tự.")
    with connection() as conn:
        with conn.transaction():
            conn.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")
            if conn.execute("SELECT 1 FROM users WHERE username=%s", (name,)).fetchone():
                raise UserError("Tên đăng nhập đã tồn tại.")
            first = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 0
            final_role = role or ("admin" if first else "user")
            return conn.execute(
                f"""INSERT INTO users (id, username, password_hash, display_name, role)
                    VALUES (%s, %s, %s, %s, %s) RETURNING {USER_COLS}""",
                (uuid.uuid4(), name, hash_password(password), (display_name or "").strip() or name, final_role),
            ).fetchone()


def authenticate(username: str, password: str) -> dict | None:
    try:
        name = normalize_username(username)
    except UserError:
        return None
    with connection() as conn:
        row = conn.execute(
            f"SELECT {USER_COLS}, password_hash FROM users WHERE username=%s", (name,)
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        conn.execute("UPDATE users SET last_login_at=now() WHERE id=%s", (row["id"],))
    row.pop("password_hash")
    return row


def _token_response(user: dict) -> dict:
    token, expires = create_access_token(str(user["id"]), user["role"])
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat(), "user": user}


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Chưa đăng nhập hoặc phiên đã hết hạn", headers={"WWW-Authenticate": "Bearer"}
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise unauthorized from None
    with connection() as conn:
        user = conn.execute(f"SELECT {USER_COLS} FROM users WHERE id=%s", (user_id,)).fetchone()
    if user is None:
        raise unauthorized
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ quản trị viên được thực hiện thao tác này")
    return user


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest):
    with connection() as conn:
        has_users = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    if has_users and not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hệ thống không cho phép tự đăng ký")
    try:
        user = create_user(req.username, req.password, req.display_name)
    except UserError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _token_response(user)


@router.post("/login")
def login(req: Credentials):
    user = authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sai tên đăng nhập hoặc mật khẩu")
    return _token_response(user)


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user


@router.get("/config")
def auth_config():
    with connection() as conn:
        has_users = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    return {
        "allow_registration": get_settings().allow_registration or not has_users,
        "min_password_length": get_settings().min_password_length,
    }

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .config import get_settings
from .db import connection
from .security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)

log = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")
USER_COLS = "id, username, display_name, role, created_at, last_login_at"
REFRESH_COOKIE = "rag_refresh"
REFRESH_COOKIE_PATH = "/api/auth"

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class RegisterRequest(Credentials):
    display_name: str | None = Field(default=None, max_length=80)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


@dataclass
class AuthContext:
    user: dict
    session_id: uuid.UUID


class UserError(ValueError):
    pass


def normalize_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME_RE.match(name):
        raise UserError("Tên đăng nhập 3-40 ký tự, chỉ gồm chữ thường không dấu, số, dấu chấm, gạch dưới, gạch ngang.")
    return name


def check_password_policy(password: str) -> None:
    min_len = get_settings().min_password_length
    if len(password) < min_len:
        raise UserError(f"Mật khẩu tối thiểu {min_len} ký tự.")


def create_user(username: str, password: str, display_name: str | None = None, role: str | None = None) -> dict:
    name = normalize_username(username)
    check_password_policy(password)
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


# ---------------------------------------------------------------- sessions & refresh tokens


def start_session(user_id: uuid.UUID, user_agent: str | None = None) -> tuple[uuid.UUID, str]:
    """Create a login session and its first refresh token. Returns (session_id, refresh_token)."""
    days = get_settings().refresh_token_days
    session_id = uuid.uuid4()
    token, token_hash = new_refresh_token()
    with connection() as conn:
        with conn.transaction():
            conn.execute(
                """DELETE FROM auth_sessions
                   WHERE expires_at < now() - interval '1 day' OR revoked_at < now() - interval '7 days'"""
            )
            conn.execute(
                """INSERT INTO auth_sessions (id, user_id, user_agent, expires_at)
                   VALUES (%s, %s, %s, now() + make_interval(days => %s))""",
                (session_id, user_id, (user_agent or "")[:300] or None, days),
            )
            conn.execute(
                "INSERT INTO refresh_tokens (token_hash, session_id) VALUES (%s, %s)", (token_hash, session_id)
            )
    return session_id, token


def rotate_refresh_token(token: str) -> tuple[dict, uuid.UUID, str | None] | None:
    """Exchange a refresh token for (user, session_id, new_refresh_token).

    The presented token is single-use. A token reused within the grace window (two tabs
    refreshing at once) still gets an access token but no new refresh token. A token reused
    after that is treated as stolen and revokes the whole session.
    """
    settings = get_settings()
    with connection() as conn:
        with conn.transaction():
            row = conn.execute(
                """SELECT rt.session_id, s.user_id,
                          rt.used_at IS NOT NULL AS used,
                          rt.used_at > now() - make_interval(secs => %s) AS in_grace,
                          s.revoked_at IS NULL AND s.expires_at > now() AS live
                   FROM refresh_tokens rt JOIN auth_sessions s ON s.id = rt.session_id
                   WHERE rt.token_hash = %s
                   FOR UPDATE OF rt, s""",
                (settings.refresh_reuse_grace_seconds, hash_refresh_token(token)),
            ).fetchone()
            if row is None or not row["live"]:
                return None
            session_id = row["session_id"]
            new_token = None
            if row["used"]:
                if not row["in_grace"]:
                    log.warning("Refresh token reuse detected, revoking session %s", session_id)
                    conn.execute("UPDATE auth_sessions SET revoked_at = now() WHERE id = %s", (session_id,))
                    return None
            else:
                new_token, new_hash = new_refresh_token()
                conn.execute("UPDATE refresh_tokens SET used_at = now() WHERE token_hash = %s",
                             (hash_refresh_token(token),))
                conn.execute(
                    "INSERT INTO refresh_tokens (token_hash, session_id) VALUES (%s, %s)", (new_hash, session_id)
                )
                conn.execute(
                    "DELETE FROM refresh_tokens WHERE session_id = %s AND used_at < now() - make_interval(days => %s)",
                    (session_id, settings.refresh_token_days),
                )
            conn.execute(
                """UPDATE auth_sessions SET last_used_at = now(), expires_at = now() + make_interval(days => %s)
                   WHERE id = %s""",
                (settings.refresh_token_days, session_id),
            )
            user = conn.execute(f"SELECT {USER_COLS} FROM users WHERE id = %s", (row["user_id"],)).fetchone()
    if user is None:
        return None
    return user, session_id, new_token


def revoke_session(session_id: uuid.UUID) -> None:
    with connection() as conn:
        conn.execute("UPDATE auth_sessions SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL", (session_id,))


def revoke_user_sessions(user_id: uuid.UUID, keep: uuid.UUID | None = None) -> int:
    with connection() as conn:
        cur = conn.execute(
            """UPDATE auth_sessions SET revoked_at = now()
               WHERE user_id = %s AND revoked_at IS NULL AND id IS DISTINCT FROM %s""",
            (user_id, keep),
        )
        return cur.rowcount


def session_for_refresh_token(token: str) -> uuid.UUID | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT session_id FROM refresh_tokens WHERE token_hash = %s", (hash_refresh_token(token),)
        ).fetchone()
    return row["session_id"] if row else None


# ---------------------------------------------------------------- HTTP helpers & dependencies


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, httponly=True, secure=settings.cookie_secure, samesite="strict"
    )


def _access_response(user: dict, session_id: uuid.UUID) -> dict:
    token, expires = create_access_token(str(user["id"]), user["role"], str(session_id))
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat(), "user": user}


def _login_response(user: dict, request: Request, response: Response) -> dict:
    session_id, refresh = start_session(user["id"], request.headers.get("user-agent"))
    _set_refresh_cookie(response, refresh)
    return _access_response(user, session_id)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Chưa đăng nhập hoặc phiên đã hết hạn", headers={"WWW-Authenticate": "Bearer"}
    )


def get_auth_context(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise _unauthorized() from None
    cols = ", ".join(f"u.{c.strip()}" for c in USER_COLS.split(","))
    with connection() as conn:
        user = conn.execute(
            f"""SELECT {cols} FROM users u JOIN auth_sessions s ON s.user_id = u.id
                WHERE u.id = %s AND s.id = %s AND s.revoked_at IS NULL AND s.expires_at > now()""",
            (user_id, session_id),
        ).fetchone()
    if user is None:
        raise _unauthorized()
    return AuthContext(user, session_id)


def get_current_user(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    return ctx.user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ quản trị viên được thực hiện thao tác này")
    return user


# ---------------------------------------------------------------- routes


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, request: Request, response: Response):
    with connection() as conn:
        has_users = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    if has_users and not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hệ thống không cho phép tự đăng ký")
    try:
        user = create_user(req.username, req.password, req.display_name)
    except UserError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _login_response(user, request, response)


@router.post("/login")
def login(req: Credentials, request: Request, response: Response):
    user = authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sai tên đăng nhập hoặc mật khẩu")
    return _login_response(user, request, response)


@router.post("/refresh")
def refresh(request: Request, response: Response):
    token = request.cookies.get(REFRESH_COOKIE)
    result = rotate_refresh_token(token) if token else None
    if result is None:
        expired = JSONResponse({"detail": "Phiên đăng nhập đã hết hạn"}, status.HTTP_401_UNAUTHORIZED)
        _clear_refresh_cookie(expired)
        return expired
    user, session_id, new_token = result
    if new_token:
        _set_refresh_cookie(response, new_token)
    return _access_response(user, session_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    session_id = None
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        session_id = session_for_refresh_token(token)
    if session_id is None and credentials is not None:
        try:
            session_id = uuid.UUID(decode_access_token(credentials.credentials)["sid"])
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            session_id = None
    if session_id is not None:
        revoke_session(session_id)
    done = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(done)
    return done


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(ctx: AuthContext = Depends(get_auth_context)):
    revoke_user_sessions(ctx.user["id"])
    done = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(done)
    return done


@router.post("/change-password")
def change_password(req: ChangePasswordRequest, ctx: AuthContext = Depends(get_auth_context)):
    try:
        check_password_policy(req.new_password)
    except UserError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    with connection() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = %s", (ctx.user["id"],)).fetchone()
        if row is None or not verify_password(req.current_password, row["password_hash"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mật khẩu hiện tại không đúng")
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                     (hash_password(req.new_password), ctx.user["id"]))
    revoked = revoke_user_sessions(ctx.user["id"], keep=ctx.session_id)
    return {"changed": True, "revoked_sessions": revoked}


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

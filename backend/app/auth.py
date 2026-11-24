from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select, text, update

from .config import get_settings
from .db import session_scope
from .models import AuthSession, RefreshToken, User
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
    with session_scope() as db:
        db.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
        if db.scalar(select(User.id).where(User.username == name)) is not None:
            raise UserError("Tên đăng nhập đã tồn tại.")
        first = db.scalar(select(func.count()).select_from(User)) == 0
        user = User(
            username=name,
            password_hash=hash_password(password),
            display_name=(display_name or "").strip() or name,
            role=role or ("admin" if first else "user"),
        )
        db.add(user)
        db.flush()
        return user.to_dict()


def authenticate(username: str, password: str) -> dict | None:
    try:
        name = normalize_username(username)
    except UserError:
        return None
    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == name))
        if user is None or not verify_password(password, user.password_hash):
            return None
        result = user.to_dict()
        db.execute(update(User).where(User.id == user.id).values(last_login_at=func.now()))
    return result


def start_session(user_id: uuid.UUID, user_agent: str | None = None) -> tuple[uuid.UUID, str]:
    days = get_settings().refresh_token_days
    token, token_hash = new_refresh_token()
    with session_scope() as db:
        db.execute(
            delete(AuthSession).where(
                or_(
                    AuthSession.expires_at < func.now() - timedelta(days=1),
                    AuthSession.revoked_at < func.now() - timedelta(days=7),
                )
            )
        )
        session = AuthSession(
            user_id=user_id,
            user_agent=(user_agent or "")[:300] or None,
            expires_at=func.now() + timedelta(days=days),
        )
        db.add(session)
        db.flush()
        db.add(RefreshToken(token_hash=token_hash, session_id=session.id))
        return session.id, token


def rotate_refresh_token(token: str) -> tuple[dict, uuid.UUID, str | None] | None:
    settings = get_settings()
    token_hash = hash_refresh_token(token)
    grace = timedelta(seconds=settings.refresh_reuse_grace_seconds)
    with session_scope() as db:
        row = db.execute(
            select(
                RefreshToken.session_id,
                AuthSession.user_id,
                RefreshToken.used_at.is_not(None).label("used"),
                (RefreshToken.used_at > func.now() - grace).label("in_grace"),
                and_(AuthSession.revoked_at.is_(None), AuthSession.expires_at > func.now()).label("live"),
            )
            .join(AuthSession, AuthSession.id == RefreshToken.session_id)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update(of=[RefreshToken, AuthSession])
        ).one_or_none()
        if row is None or not row.live:
            return None
        session_id = row.session_id
        new_token = None
        if row.used:
            if not row.in_grace:
                log.warning("Refresh token reuse detected, revoking session %s", session_id)
                db.execute(update(AuthSession).where(AuthSession.id == session_id).values(revoked_at=func.now()))
                return None
        else:
            new_token, new_hash = new_refresh_token()
            db.execute(update(RefreshToken).where(RefreshToken.token_hash == token_hash).values(used_at=func.now()))
            db.add(RefreshToken(token_hash=new_hash, session_id=session_id))
            db.flush()
            db.execute(
                delete(RefreshToken).where(
                    RefreshToken.session_id == session_id,
                    RefreshToken.used_at < func.now() - timedelta(days=settings.refresh_token_days),
                )
            )
        db.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id)
            .values(last_used_at=func.now(), expires_at=func.now() + timedelta(days=settings.refresh_token_days))
        )
        user = db.get(User, row.user_id)
        if user is None:
            return None
        return user.to_dict(), session_id, new_token


def revoke_session(session_id: uuid.UUID) -> None:
    with session_scope() as db:
        db.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )


def revoke_user_sessions(user_id: uuid.UUID, keep: uuid.UUID | None = None) -> int:
    conditions = [AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)]
    if keep is not None:
        conditions.append(AuthSession.id != keep)
    with session_scope() as db:
        return db.execute(update(AuthSession).where(*conditions).values(revoked_at=func.now())).rowcount


def session_for_refresh_token(token: str) -> uuid.UUID | None:
    with session_scope() as db:
        return db.scalar(select(RefreshToken.session_id).where(RefreshToken.token_hash == hash_refresh_token(token)))


def has_users() -> bool:
    with session_scope() as db:
        return db.scalar(select(User.id).limit(1)) is not None


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
    with session_scope() as db:
        found = db.scalar(
            select(User)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                User.id == user_id,
                AuthSession.id == session_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > func.now(),
            )
        )
        user = found.to_dict() if found is not None else None
    if user is None:
        raise _unauthorized()
    return AuthContext(user, session_id)


def get_current_user(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    return ctx.user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ quản trị viên được thực hiện thao tác này")
    return user


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, request: Request, response: Response):
    if has_users() and not get_settings().allow_registration:
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
    with session_scope() as db:
        user = db.get(User, ctx.user["id"], with_for_update=True)
        if user is None or not verify_password(req.current_password, user.password_hash):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mật khẩu hiện tại không đúng")
        user.password_hash = hash_password(req.new_password)
    revoked = revoke_user_sessions(ctx.user["id"], keep=ctx.session_id)
    return {"changed": True, "revoked_sessions": revoked}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user


@router.get("/config")
def auth_config():
    return {
        "allow_registration": get_settings().allow_registration or not has_users(),
        "min_password_length": get_settings().min_password_length,
    }

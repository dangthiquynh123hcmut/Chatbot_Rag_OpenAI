from __future__ import annotations

import logging
import re
import uuid

import jwt
from fastapi import status

from app.config import get_settings
from app.core.exceptions import AppException, BadRequestError, ForbiddenError, UnauthorizedError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models.user_model import User
from app.repositories.interfaces.i_auth_session_repository import IAuthSessionRepository
from app.repositories.interfaces.i_user_repository import IUserRepository
from app.schemas.auth_schema import (
    AuthConfigResponse,
    AuthContext,
    ChangePasswordResponse,
    LoginResult,
    TokenResponse,
    UserResponse,
)
from app.services.interfaces.i_auth_service import IAuthService

log = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")


class UserError(BadRequestError):
    """Invalid username / password given when creating an account or changing a password."""


def normalize_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME_RE.match(name):
        raise UserError("Tên đăng nhập 3-40 ký tự, chỉ gồm chữ thường không dấu, số, dấu chấm, gạch dưới, gạch ngang.")
    return name


def check_password_policy(password: str) -> None:
    min_len = get_settings().min_password_length
    if len(password) < min_len:
        raise UserError(f"Mật khẩu tối thiểu {min_len} ký tự.")


class AuthService(IAuthService):
    def __init__(self, users: IUserRepository, sessions: IAuthSessionRepository) -> None:
        self.users = users
        self.sessions = sessions

    # ============ Accounts ============

    def create_user(
        self, username: str, password: str, display_name: str | None = None, role: str | None = None
    ) -> UserResponse:
        name = normalize_username(username)
        check_password_policy(password)
        self.users.lock_table()
        if self.users.username_exists(name):
            raise UserError("Tên đăng nhập đã tồn tại.")
        first = self.users.count() == 0
        user = self.users.add(
            User(
                username=name,
                password_hash=hash_password(password),
                display_name=(display_name or "").strip() or name,
                role=role or ("admin" if first else "user"),
            )
        )
        return UserResponse.model_validate(user)

    def register(
        self, username: str, password: str, display_name: str | None, user_agent: str | None
    ) -> LoginResult:
        if self.users.any_exists() and not get_settings().allow_registration:
            raise ForbiddenError("Hệ thống không cho phép tự đăng ký")
        user = self.create_user(username, password, display_name)
        return self._start_session(user, user_agent)

    def auth_config(self) -> AuthConfigResponse:
        settings = get_settings()
        return AuthConfigResponse(
            allow_registration=settings.allow_registration or not self.users.any_exists(),
            min_password_length=settings.min_password_length,
        )

    # ============ Login sessions ============

    def login(self, username: str, password: str, user_agent: str | None) -> LoginResult:
        user = self._check_credentials(username, password)
        if user is None:
            raise AppException("Sai tên đăng nhập hoặc mật khẩu", status.HTTP_401_UNAUTHORIZED)
        return self._start_session(user, user_agent)

    def refresh(self, refresh_token: str | None) -> LoginResult | None:
        if not refresh_token:
            return None
        settings = get_settings()
        token_hash = hash_refresh_token(refresh_token)
        state = self.sessions.get_refresh_state(token_hash, settings.refresh_reuse_grace_seconds)
        if state is None or not state.live:
            return None
        new_token = None
        if state.used:
            if not state.in_grace:
                # Replay of a rotated token = theft: end the whole session
                log.warning("Refresh token reuse detected, revoking session %s", state.session_id)
                self.sessions.revoke(state.session_id)
                return None
        else:
            new_token, new_hash = new_refresh_token()
            self.sessions.mark_token_used(token_hash)
            self.sessions.add_refresh_token(new_hash, state.session_id)
            self.sessions.delete_used_tokens(state.session_id, settings.refresh_token_days)
        self.sessions.extend(state.session_id, settings.refresh_token_days)
        user = self.users.get_by_id(state.user_id)
        if user is None:
            return None
        return LoginResult(self._tokens(UserResponse.model_validate(user), state.session_id), new_token)

    def logout(self, refresh_token: str | None, access_token: str | None) -> None:
        session_id = self.sessions.session_id_for_token(hash_refresh_token(refresh_token)) if refresh_token else None
        if session_id is None and access_token is not None:
            try:
                session_id = uuid.UUID(decode_access_token(access_token)["sid"])
            except (jwt.PyJWTError, ValueError, KeyError, TypeError):
                session_id = None
        if session_id is not None:
            self.sessions.revoke(session_id)

    def logout_all(self, user_id: uuid.UUID) -> int:
        return self.sessions.revoke_for_user(user_id)

    def change_password(self, context: AuthContext, current_password: str, new_password: str) -> ChangePasswordResponse:
        check_password_policy(new_password)
        user = self.users.get_by_id(context.user.id, lock=True)
        if user is None or not verify_password(current_password, user.password_hash):
            raise BadRequestError("Mật khẩu hiện tại không đúng")
        user.password_hash = hash_password(new_password)
        revoked = self.sessions.revoke_for_user(context.user.id, keep=context.session_id)
        return ChangePasswordResponse(changed=True, revoked_sessions=revoked)

    def authenticate(self, access_token: str) -> AuthContext:
        try:
            payload = decode_access_token(access_token)
            user_id = uuid.UUID(payload["sub"])
            session_id = uuid.UUID(payload["sid"])
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            raise UnauthorizedError() from None
        user = self.sessions.find_active_user(user_id, session_id)
        if user is None:
            raise UnauthorizedError()
        return AuthContext(UserResponse.model_validate(user), session_id)

    # ============ Helpers ============

    def _check_credentials(self, username: str, password: str) -> UserResponse | None:
        try:
            name = normalize_username(username)
        except UserError:
            return None
        user = self.users.get_by_username(name)
        if user is None or not verify_password(password, user.password_hash):
            return None
        # The response shows the previous login
        result = UserResponse.model_validate(user)
        self.users.touch_last_login(user.id)
        return result

    def _start_session(self, user: UserResponse, user_agent: str | None) -> LoginResult:
        self.sessions.purge_stale()
        auth_session = self.sessions.create(user.id, user_agent, get_settings().refresh_token_days)
        token, token_hash = new_refresh_token()
        self.sessions.add_refresh_token(token_hash, auth_session.id)
        return LoginResult(self._tokens(user, auth_session.id), token)

    @staticmethod
    def _tokens(user: UserResponse, session_id: uuid.UUID) -> TokenResponse:
        token, expires = create_access_token(str(user.id), user.role, str(session_id))
        return TokenResponse(access_token=token, expires_at=expires.isoformat(), user=user)

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.schemas.auth_schema import AuthConfigResponse, AuthContext, ChangePasswordResponse, LoginResult, UserResponse


class IAuthService(ABC):
    @abstractmethod
    def create_user(
        self, username: str, password: str, display_name: str | None = None, role: str | None = None
    ) -> UserResponse:
        """New account; the first one is an admin unless `role` says otherwise. Raises UserError."""

    @abstractmethod
    def register(
        self, username: str, password: str, display_name: str | None, user_agent: str | None
    ) -> LoginResult:
        """Self-registration (if allowed) followed by a login."""

    @abstractmethod
    def login(self, username: str, password: str, user_agent: str | None) -> LoginResult:
        pass

    @abstractmethod
    def refresh(self, refresh_token: str | None) -> LoginResult | None:
        """Rotate the refresh token; None when it is unknown, expired, revoked or replayed (theft:
        the whole session is revoked)."""

    @abstractmethod
    def logout(self, refresh_token: str | None, access_token: str | None) -> None:
        pass

    @abstractmethod
    def logout_all(self, user_id: uuid.UUID) -> int:
        pass

    @abstractmethod
    def change_password(self, context: AuthContext, current_password: str, new_password: str) -> ChangePasswordResponse:
        """Also revokes every other session of the user."""

    @abstractmethod
    def authenticate(self, access_token: str) -> AuthContext:
        """The user of a valid access token whose session is live. Raises UnauthorizedError."""

    @abstractmethod
    def auth_config(self) -> AuthConfigResponse:
        pass

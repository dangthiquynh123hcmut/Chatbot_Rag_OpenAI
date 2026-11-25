from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.user_model import AuthSession, User


@dataclass
class RefreshTokenState:
    """State of a presented refresh token and of its session, read with both rows locked."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    used: bool
    # Used within the reuse grace period (two tabs refreshing at once)
    in_grace: bool
    # Session neither revoked nor expired
    live: bool


class IAuthSessionRepository(ABC):
    @abstractmethod
    def purge_stale(self) -> None:
        """Delete sessions expired for a day or revoked for a week."""

    @abstractmethod
    def create(self, user_id: uuid.UUID, user_agent: str | None, days: int) -> AuthSession:
        pass

    @abstractmethod
    def add_refresh_token(self, token_hash: str, session_id: uuid.UUID) -> None:
        pass

    @abstractmethod
    def get_refresh_state(self, token_hash: str, grace_seconds: int) -> RefreshTokenState | None:
        pass

    @abstractmethod
    def mark_token_used(self, token_hash: str) -> None:
        pass

    @abstractmethod
    def delete_used_tokens(self, session_id: uuid.UUID, older_than_days: int) -> None:
        pass

    @abstractmethod
    def extend(self, session_id: uuid.UUID, days: int) -> None:
        """Mark the session used now and push its expiry `days` ahead."""

    @abstractmethod
    def revoke(self, session_id: uuid.UUID) -> None:
        pass

    @abstractmethod
    def revoke_for_user(self, user_id: uuid.UUID, keep: uuid.UUID | None = None) -> int:
        """Revoke every live session of the user except `keep`; the number revoked."""

    @abstractmethod
    def session_id_for_token(self, token_hash: str) -> uuid.UUID | None:
        pass

    @abstractmethod
    def find_active_user(self, user_id: uuid.UUID, session_id: uuid.UUID) -> User | None:
        """The user, if `session_id` is one of its live sessions."""

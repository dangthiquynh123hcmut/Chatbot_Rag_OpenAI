from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.user_model import AuthSession, RefreshToken, User
from app.repositories.interfaces.i_auth_session_repository import IAuthSessionRepository, RefreshTokenState


class AuthSessionRepository(IAuthSessionRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def purge_stale(self) -> None:
        self.session.execute(
            delete(AuthSession).where(
                or_(
                    AuthSession.expires_at < func.now() - timedelta(days=1),
                    AuthSession.revoked_at < func.now() - timedelta(days=7),
                )
            )
        )

    def create(self, user_id: uuid.UUID, user_agent: str | None, days: int) -> AuthSession:
        auth_session = AuthSession(
            user_id=user_id,
            user_agent=(user_agent or "")[:300] or None,
            expires_at=func.now() + timedelta(days=days),
        )
        self.session.add(auth_session)
        self.session.flush()
        return auth_session

    def add_refresh_token(self, token_hash: str, session_id: uuid.UUID) -> None:
        self.session.add(RefreshToken(token_hash=token_hash, session_id=session_id))
        self.session.flush()

    def get_refresh_state(self, token_hash: str, grace_seconds: int) -> RefreshTokenState | None:
        grace = timedelta(seconds=grace_seconds)
        row = self.session.execute(
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
        if row is None:
            return None
        return RefreshTokenState(
            session_id=row.session_id,
            user_id=row.user_id,
            used=bool(row.used),
            in_grace=bool(row.in_grace),
            live=bool(row.live),
        )

    def mark_token_used(self, token_hash: str) -> None:
        self.session.execute(
            update(RefreshToken).where(RefreshToken.token_hash == token_hash).values(used_at=func.now())
        )

    def delete_used_tokens(self, session_id: uuid.UUID, older_than_days: int) -> None:
        self.session.execute(
            delete(RefreshToken).where(
                RefreshToken.session_id == session_id,
                RefreshToken.used_at < func.now() - timedelta(days=older_than_days),
            )
        )

    def extend(self, session_id: uuid.UUID, days: int) -> None:
        self.session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id)
            .values(last_used_at=func.now(), expires_at=func.now() + timedelta(days=days))
        )

    def revoke(self, session_id: uuid.UUID) -> None:
        self.session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )

    def revoke_for_user(self, user_id: uuid.UUID, keep: uuid.UUID | None = None) -> int:
        conditions = [AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)]
        if keep is not None:
            conditions.append(AuthSession.id != keep)
        result = self.session.execute(update(AuthSession).where(*conditions).values(revoked_at=func.now()))
        return result.rowcount

    def session_id_for_token(self, token_hash: str) -> uuid.UUID | None:
        return self.session.scalar(select(RefreshToken.session_id).where(RefreshToken.token_hash == token_hash))

    def find_active_user(self, user_id: uuid.UUID, session_id: uuid.UUID) -> User | None:
        return self.session.scalar(
            select(User)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                User.id == user_id,
                AuthSession.id == session_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > func.now(),
            )
        )

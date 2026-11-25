from __future__ import annotations

import uuid

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.models.user_model import User
from app.repositories.interfaces.i_user_repository import IUserRepository


class UserRepository(IUserRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_table(self) -> None:
        self.session.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))

    def get_by_id(self, user_id: uuid.UUID, lock: bool = False) -> User | None:
        return self.session.get(User, user_id, with_for_update=lock)

    def get_by_username(self, username: str) -> User | None:
        return self.session.scalar(select(User).where(User.username == username))

    def username_exists(self, username: str) -> bool:
        return self.session.scalar(select(User.id).where(User.username == username)) is not None

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(User)) or 0)

    def any_exists(self) -> bool:
        return self.session.scalar(select(User.id).limit(1)) is not None

    def add(self, user: User) -> User:
        self.session.add(user)
        self.session.flush()
        return user

    def touch_last_login(self, user_id: uuid.UUID) -> None:
        # The loaded user keeps its previous last_login_at (shown at login)
        self.session.execute(
            update(User).where(User.id == user_id).values(last_login_at=func.now()),
            execution_options={"synchronize_session": False},
        )

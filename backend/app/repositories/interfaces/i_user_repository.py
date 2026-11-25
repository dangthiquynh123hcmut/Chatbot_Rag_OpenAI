from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.models.user_model import User


class IUserRepository(ABC):
    @abstractmethod
    def lock_table(self) -> None:
        """Serialize account creation until the transaction ends (unique name, first user = admin)."""

    @abstractmethod
    def get_by_id(self, user_id: uuid.UUID, lock: bool = False) -> User | None:
        pass

    @abstractmethod
    def get_by_username(self, username: str) -> User | None:
        pass

    @abstractmethod
    def username_exists(self, username: str) -> bool:
        pass

    @abstractmethod
    def count(self) -> int:
        pass

    @abstractmethod
    def any_exists(self) -> bool:
        pass

    @abstractmethod
    def add(self, user: User) -> User:
        pass

    @abstractmethod
    def touch_last_login(self, user_id: uuid.UUID) -> None:
        pass

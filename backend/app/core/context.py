"""Database session of a request (set by SessionMiddleware), and factories for code that runs outside one."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar

from sqlalchemy.orm import Session

from app.core.database import Database

# Opens a NEW session committed / closed when the `with` block ends
SessionFactory = Callable[[], AbstractContextManager[Session]]


def create_session_factory(db: Database) -> SessionFactory:
    """Session factory for code that runs outside an HTTP request (background ingest, retrieval,
    CLI, evaluation), or that must not keep the request transaction open (LLM calls)."""
    return db.session_scope


_current_session: ContextVar[Session | None] = ContextVar("current_session", default=None)


def get_current_session() -> Session:
    session = _current_session.get()
    if session is None:
        raise RuntimeError("No database session in context. Ensure SessionMiddleware is properly configured.")
    return session


def set_current_session(session: Session) -> None:
    _current_session.set(session)


def clear_current_session() -> None:
    _current_session.set(None)


@contextmanager
def session_context(session_factory: SessionFactory) -> Iterator[Session]:
    """Use the request-scoped providers outside a request (CLI, tests): a new session, set as the
    current one and committed when the block ends."""
    with session_factory() as session:
        token = _current_session.set(session)
        try:
            yield session
        finally:
            _current_session.reset(token)

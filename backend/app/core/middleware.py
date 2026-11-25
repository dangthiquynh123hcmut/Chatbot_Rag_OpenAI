"""Middleware module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import clear_current_session, set_current_session
from app.core.database import Database


class SessionMiddleware(BaseHTTPMiddleware):
    """One database session per request: committed when the request ends without an unhandled
    exception (AppException responses included), rolled back otherwise."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        db: Database = request.app.state.container.db()
        session = db.create_session()
        set_current_session(session)
        try:
            response = await call_next(request)
            # Sync session: keep the blocking I/O off the event loop
            await run_in_threadpool(session.commit)
            return response
        except Exception:
            await run_in_threadpool(session.rollback)
            raise
        finally:
            await run_in_threadpool(session.close)
            clear_current_session()

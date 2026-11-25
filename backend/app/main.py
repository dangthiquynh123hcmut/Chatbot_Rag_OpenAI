from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.containers import Container
from app.core.context import get_current_session
from app.core.database import init_db
from app.core.exceptions import AppException
from app.core.middleware import SessionMiddleware
from app.routers.auth_router import router as auth_router
from app.routers.conversation_router import router as conversation_router
from app.routers.document_router import router as document_router
from app.routers.query_router import router as query_router
from app.routers.section_router import router as section_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    container = Container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: migrations + embedding dimension check
        await run_in_threadpool(init_db, container.db())
        yield
        # Shutdown: close the pool; the next startup (tests) reads the settings again
        container.db().close()
        container.reset_singletons()

    app = FastAPI(title="Hierarchy-Aware RAG", version="1.0.0", lifespan=lifespan)
    app.state.container = container

    app.add_middleware(SessionMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (auth_router, document_router, section_router, conversation_router, query_router):
        app.include_router(router, prefix="/api")

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code, headers=exc.headers)

    return app


app = create_app()


@app.get("/api/health")
def health(session: Annotated[Session, Depends(get_current_session)]) -> dict:
    session.execute(text("SELECT 1"))
    s = get_settings()
    return {
        "status": "ok",
        "provider": s.llm_provider,
        "llm_model": s.active_llm_model,
        "embedding_model": s.active_embedding_model,
        "embedding_dim": s.embedding_dim,
        "reranker": s.reranker,
        "aws_region": s.aws_region if s.llm_provider == "bedrock" else None,
    }

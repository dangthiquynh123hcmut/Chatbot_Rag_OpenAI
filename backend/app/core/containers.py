"""Dependency injection container."""

from __future__ import annotations

from dependency_injector import containers, providers

from app.core.context import create_session_factory, get_current_session
from app.core.database import Database
from app.repositories.auth_session_repository import AuthSessionRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.section_repository import SectionRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.conversation_service import ConversationService
from app.services.document_service import DocumentService
from app.services.ingest_service import IngestService
from app.services.query_service import QueryService
from app.services.search_backend import DbSearchBackend


class Container(containers.DeclarativeContainer):
    """Application dependency injection container."""

    wiring_config = containers.WiringConfiguration(packages=["app.routers"])

    # -------------------------------------------------------------------------
    # Infrastructure
    # -------------------------------------------------------------------------
    # Created on first use from the settings; closed and reset at shutdown (main.py)
    db = providers.Singleton(Database.from_settings)

    # Session of the current request (SessionMiddleware) or of core.context.session_context
    session = providers.Factory(get_current_session)

    # New session per call, for code that runs outside a request or must not hold its transaction
    session_factory_provider = providers.Singleton(create_session_factory, db=db)

    # -------------------------------------------------------------------------
    # Repositories (request session)
    # -------------------------------------------------------------------------
    user_repository_provider = providers.Factory(UserRepository, session=session)
    auth_session_repository_provider = providers.Factory(AuthSessionRepository, session=session)
    conversation_repository_provider = providers.Factory(ConversationRepository, session=session)
    document_repository_provider = providers.Factory(DocumentRepository, session=session)
    section_repository_provider = providers.Factory(SectionRepository, session=session)

    # -------------------------------------------------------------------------
    # RAG (own short sessions)
    # -------------------------------------------------------------------------
    search_backend_provider = providers.Singleton(DbSearchBackend, session_factory=session_factory_provider)

    query_service_provider = providers.Factory(QueryService, backend=search_backend_provider)

    # Background ingestion outlives the request that scheduled it
    ingest_service_provider = providers.Singleton(IngestService, session_factory=session_factory_provider)

    # -------------------------------------------------------------------------
    # Services (request session)
    # -------------------------------------------------------------------------
    auth_service_provider = providers.Factory(
        AuthService,
        users=user_repository_provider,
        sessions=auth_session_repository_provider,
    )

    document_service_provider = providers.Factory(
        DocumentService,
        session=session,
        documents=document_repository_provider,
        sections=section_repository_provider,
    )

    conversation_service_provider = providers.Factory(
        ConversationService,
        session=session,
        repository=conversation_repository_provider,
        query_service=query_service_provider,
    )

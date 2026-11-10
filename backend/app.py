"""FastAPI entry point for the knowledge agent service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from backend.api.auth import (
    AuthHttpSettings,
    app_database_unavailable,
    auth_storage_unavailable,
    auth_validation_error,
)
from backend.api.auth import router as auth_router
from backend.api.conversations import router as conversations_router
from backend.api.speech import router as speech_router
from backend.api.document_uploads import router as document_uploads_router
from backend.api.files import router as files_router
from backend.api.inquiries import experts_router, router as inquiries_router
from backend.api.knowledge_agent import KnowledgeAgentPool
from backend.api.knowledge_agent import router as knowledge_agent_router
from backend.api.knowledge_bases import KnowledgeBaseCatalog
from backend.api.knowledge_bases import router as knowledge_bases_router
from backend.api.knowledge_management import router as knowledge_management_router
from backend.api.knowledge_search import router as knowledge_search_router
from backend.api.voice_capture import VoiceCaptureService
from backend.api.voice_capture import router as voice_capture_router
from backend.knowledge_bases import KNOWLEDGE_BASES
from backend.src.clients.agent import AgentClient, AgentSettings
from backend.src.storage.app_database import (
    AppDatabaseUnavailable,
    app_database_url_from_env,
    create_app_engine,
    create_session_factory,
)
from backend.src.storage.auth import AuthStorageUnavailable, AuthStore
from backend.src.services.inquiries import InquiryService
from backend.src.storage.conversations import ConversationStore
from backend.src.storage.inquiries import InquiryStore
from backend.src.storage.document_uploads import DocumentUploadStore
from backend.src.storage.embeddings import EmbeddingMismatch, verify_embedding_dimension
from backend.src.storage.file_library import FileBlobStore, FileRecordStore
from backend.src.services.auth import AuthService
from backend.src.services.demo_scenario import (
    DemoAgentClient,
    ensure_demo_expert,
    load_demo_scenario,
)
from backend.src.services.file_library import FileLibraryService
from backend.src.storage.repositories.lifecycle import InactiveKnowledgeBase, InactiveSource
from backend.src.storage.repositories.chunks import ChunkRepository
from backend.src.storage.repositories.base import KnowledgeStorageUnavailable
from backend.src.storage.repositories.ingestion_jobs import IngestionJobRepository
from backend.src.storage.repositories.knowledge_bases import KnowledgeBaseRepository
from backend.src.storage.repositories.sources import SourceRepository
from backend.src.services.application_knowledge_management import (
    ApplicationKnowledgeManagementService,
)
from backend.src.services.chunk_search import ChunkSearchService
from backend.src.services.knowledge_management import KnowledgeManagementService


async def knowledge_storage_unavailable(
    _request: Request, _exc: KnowledgeStorageUnavailable
) -> JSONResponse:
    """Return a stable 503 without exposing database connection details."""

    logging.getLogger(__name__).warning(
        "Application knowledge storage is unavailable", exc_info=_exc
    )
    return JSONResponse(
        {"detail": "Application storage is temporarily unavailable"},
        status_code=503,
    )


def create_app(
    *,
    knowledge_agents: Mapping[str, Any] | None = None,
    voice_capture: VoiceCaptureService | None = None,
    kb_catalog: KnowledgeBaseCatalog | None = None,
    document_uploads: DocumentUploadStore | None = None,
    knowledge_management: KnowledgeManagementService | None = None,
    auth_service: AuthService | None = None,
    auth_settings: AuthHttpSettings | None = None,
    conversation_store: ConversationStore | None = None,
    agent_client: Any | None = None,
    chat_speech: Any | None = None,
    file_library: FileLibraryService | None = None,
    inquiry_service: InquiryService | None = None,
    sources: SourceRepository | None = None,
    chunk_search: ChunkSearchService | None = None,
) -> FastAPI:
    """Build the API; injected agents and services keep HTTP tests dependency-free.

    With injected knowledge services, auth, conversations and the file library
    use only the injected `auth_service`, `conversation_store`, `agent_client`,
    `file_library` and `inquiry_service` (routes return 503 without them) and never open the
    application database or the agent. Otherwise they use APP_DATABASE_URL,
    AGENT_BASE_URL and AGENT_API_TOKEN; the database services share one
    connection pool with the knowledge repositories. Without APP_DATABASE_URL,
    database-backed routes remain unavailable while the API can still start.
    Incompatible KB embeddings disable knowledge routes without disabling chat
    or the personal file library; fixing that configuration requires a restart.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Wire shared services and close resources owned by this application."""

        if knowledge_agents is not None or voice_capture is not None or kb_catalog is not None or knowledge_management is not None:
            app.state.knowledge_agents = KnowledgeAgentPool({}, agents=knowledge_agents or {})
            app.state.voice_capture = voice_capture or VoiceCaptureService({})
            app.state.kb_catalog = kb_catalog
            app.state.document_uploads = document_uploads or DocumentUploadStore()
            app.state.sources = sources
            app.state.chunk_search = chunk_search
            manager = knowledge_management or KnowledgeManagementService(
                KNOWLEDGE_BASES, app.state.document_uploads, app.state.voice_capture,
            )
            app.state.knowledge_management = manager
            app.state.auth_service = auth_service
            app.state.auth_settings = auth_settings
            app.state.conversation_store = conversation_store
            app.state.agent_client = agent_client
            app.state.chat_speech = chat_speech
            app.state.file_library = file_library
            app.state.inquiry_service = inquiry_service
            try:
                yield
            finally:
                await manager.aclose()
            return

        load_dotenv()
        app.state.auth_settings = auth_settings or AuthHttpSettings.from_env()
        app_engine = None
        app.state.auth_service = auth_service
        app.state.conversation_store = conversation_store
        app.state.file_library = file_library
        app.state.inquiry_service = inquiry_service
        url = app_database_url_from_env()
        needs_database = (
            auth_service is None or conversation_store is None or file_library is None
            or inquiry_service is None
        )
        sessions = None
        knowledge_ready = False
        if url:
            # Keep the shared pool available even when KB validation fails below.
            app_engine = create_app_engine(url)
            sessions = create_session_factory(app_engine)
            try:
                await verify_embedding_dimension(sessions)
            except (EmbeddingMismatch, ValueError, SQLAlchemyError, OSError) as exc:
                logging.getLogger(__name__).warning(
                    "Knowledge storage is unavailable; chat and file services remain configured: %s",
                    type(exc).__name__,
                )
            except BaseException:
                await app_engine.dispose()
                raise
            else:
                knowledge_ready = True
            app.state.auth_service = auth_service or AuthService(AuthStore(sessions))
            app.state.conversation_store = conversation_store or ConversationStore(sessions)
            app.state.file_library = file_library or FileLibraryService(
                FileRecordStore(sessions), FileBlobStore()
            )
            app.state.inquiry_service = inquiry_service or InquiryService(
                InquiryStore(sessions), app.state.conversation_store, app.state.file_library
            )
        elif not url and needs_database:
            logging.getLogger(__name__).warning(
                "APP_DATABASE_URL is not set; authentication, conversations or the file library are unavailable"
            )
        owned_agent_client = None
        app.state.agent_client = agent_client
        app.state.chat_speech = chat_speech
        if agent_client is None:
            agent_settings = AgentSettings.from_env()
            if agent_settings is not None:
                owned_agent_client = AgentClient(agent_settings)
                app.state.agent_client = owned_agent_client
            else:
                logging.getLogger(__name__).warning(
                    "AGENT_BASE_URL or AGENT_API_TOKEN is not set; agent chat is unavailable"
                )
        demo_scenario = load_demo_scenario()
        if demo_scenario is not None:
            # DEMO_MODE: the scripted prompt is answered here; other messages reach the agent.
            app.state.agent_client = DemoAgentClient(app.state.agent_client, demo_scenario)
            if sessions is not None:
                try:
                    await ensure_demo_expert(sessions, demo_scenario.expert)
                except (AppDatabaseUnavailable, SQLAlchemyError):
                    logging.getLogger(__name__).warning(
                        "Demo mode: the mock expert account could not be created"
                    )
        app.state.document_uploads = document_uploads or DocumentUploadStore()
        catalog = None
        pool = KnowledgeAgentPool({})
        capture = VoiceCaptureService({})
        manager = None
        app.state.sources = sources
        app.state.chunk_search = chunk_search
        if sessions is not None and knowledge_ready:
            source_repository = sources or SourceRepository(sessions)
            app.state.sources = source_repository
            app.state.chunk_search = chunk_search or ChunkSearchService(
                sessions, ChunkRepository(sessions), source_repository
            )
            catalog = KnowledgeBaseCatalog(KnowledgeBaseRepository(sessions))
            pool = KnowledgeAgentPool({}, sessions=sessions, catalog=catalog)
            capture = VoiceCaptureService(
                {}, sources=source_repository, resolve_kb=catalog.resolve
            )
            catalog.add_listener(pool)
            catalog.add_listener(capture)
            manager = knowledge_management or ApplicationKnowledgeManagementService(
                catalog,
                source_repository,
                ChunkRepository(sessions),
                IngestionJobRepository(sessions),
                sessions,
            )
            capture.on_ingested = manager.queue_conflicts
            try:
                await catalog.ensure_loaded()
            except Exception:
                logging.getLogger(__name__).warning(
                    "Could not load application knowledge bases", exc_info=True
                )
        app.state.knowledge_agents = pool
        app.state.voice_capture = capture
        app.state.kb_catalog = catalog
        app.state.knowledge_management = manager
        if catalog is not None:
            catalog.start_listener(url)
        try:
            yield
        finally:
            if catalog is not None:
                await catalog.aclose()
            if manager is not None:
                await manager.aclose()
            await capture.aclose()
            await pool.aclose()
            if owned_agent_client is not None:
                await owned_agent_client.aclose()
            if app_engine is not None:
                await app_engine.dispose()

    api = FastAPI(
        title="Knowledge Agent Service",
        version="1.0.0",
        lifespan=lifespan,
    )
    api.add_exception_handler(AuthStorageUnavailable, auth_storage_unavailable)
    api.add_exception_handler(AppDatabaseUnavailable, app_database_unavailable)
    api.add_exception_handler(
        KnowledgeStorageUnavailable, knowledge_storage_unavailable
    )

    @api.exception_handler(InactiveKnowledgeBase)
    @api.exception_handler(InactiveSource)
    async def inactive_ingestion_target(_request: Request, exc: LookupError) -> JSONResponse:
        """Return 404 when a concurrent deletion invalidates a write target."""

        detail = "Knowledge base not found" if isinstance(exc, InactiveKnowledgeBase) else "Source not found"
        return JSONResponse(status_code=404, content={"detail": detail})

    api.add_exception_handler(RequestValidationError, auth_validation_error)
    api.include_router(auth_router)
    api.include_router(conversations_router)
    api.include_router(speech_router)
    api.include_router(knowledge_agent_router)
    api.include_router(knowledge_bases_router)
    api.include_router(voice_capture_router)
    api.include_router(document_uploads_router)
    api.include_router(files_router)
    api.include_router(inquiries_router)
    api.include_router(experts_router)
    api.include_router(knowledge_management_router)
    api.include_router(knowledge_search_router)

    @api.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return api


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="0.0.0.0", port=8005, reload=True)

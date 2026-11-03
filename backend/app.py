"""FastAPI entry point for the knowledge agent service."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

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
from backend.api.knowledge_agent import KnowledgeAgentPool
from backend.api.knowledge_agent import router as knowledge_agent_router
from backend.api.knowledge_bases import KnowledgeBaseCatalog
from backend.api.knowledge_bases import router as knowledge_bases_router
from backend.api.knowledge_management import router as knowledge_management_router
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
from backend.src.storage.conversations import ConversationStore
from backend.src.storage.document_uploads import DocumentUploadStore
from backend.src.services.auth import AuthService
from backend.src.services.knowledge_management import KnowledgeManagementService


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
) -> FastAPI:
    """Build the API; injected agents and services keep HTTP tests dependency-free.

    With injected knowledge services, auth and conversations use only the
    injected `auth_service`, `conversation_store` and `agent_client` (routes
    return 503 without them) and never open the application database or the
    agent. Otherwise they use APP_DATABASE_URL, AGENT_BASE_URL and AGENT_API_TOKEN.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if knowledge_agents is not None or voice_capture is not None or kb_catalog is not None or knowledge_management is not None:
            app.state.knowledge_agents = KnowledgeAgentPool({}, agents=knowledge_agents or {})
            app.state.voice_capture = voice_capture or VoiceCaptureService({})
            app.state.kb_catalog = kb_catalog
            app.state.document_uploads = document_uploads or DocumentUploadStore()
            manager = knowledge_management or KnowledgeManagementService(
                KNOWLEDGE_BASES, app.state.document_uploads, app.state.voice_capture,
            )
            app.state.knowledge_management = manager
            app.state.auth_service = auth_service
            app.state.auth_settings = auth_settings
            app.state.conversation_store = conversation_store
            app.state.agent_client = agent_client
            app.state.chat_speech = chat_speech
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
        url = app_database_url_from_env()
        if url and (auth_service is None or conversation_store is None):
            # Connects lazily; an unreachable database fails requests with 503.
            app_engine = create_app_engine(url)
            sessions = create_session_factory(app_engine)
            app.state.auth_service = auth_service or AuthService(AuthStore(sessions))
            app.state.conversation_store = conversation_store or ConversationStore(sessions)
        elif not url and (auth_service is None or conversation_store is None):
            logging.getLogger(__name__).warning(
                "APP_DATABASE_URL is not set; authentication or conversations are unavailable"
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
        app.state.document_uploads = document_uploads or DocumentUploadStore()
        # Each knowledge base's store and agent open on its first request.
        pool = KnowledgeAgentPool(KNOWLEDGE_BASES)
        capture = VoiceCaptureService(KNOWLEDGE_BASES)
        manager = knowledge_management or KnowledgeManagementService(
            KNOWLEDGE_BASES, app.state.document_uploads, capture,
        )
        app.state.knowledge_management = manager
        # In-process (legacy) extractions queue the conflict stage too.
        capture.on_ingested = manager.queue_conflicts
        # Knowledge bases created in the UI are added to both services.
        catalog = KnowledgeBaseCatalog(KNOWLEDGE_BASES, listeners=[pool, capture, manager])
        try:
            await catalog.ensure_loaded()
        except Exception:
            # Retried on the next list request; built-in knowledge bases still work.
            logging.getLogger(__name__).warning(
                "Could not load created knowledge bases", exc_info=True
            )
        app.state.knowledge_agents = pool
        app.state.voice_capture = capture
        app.state.kb_catalog = catalog
        try:
            yield
        finally:
            await manager.aclose()
            await capture.aclose()
            await pool.aclose()
            await catalog.aclose()
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
    api.add_exception_handler(RequestValidationError, auth_validation_error)
    api.include_router(auth_router)
    api.include_router(conversations_router)
    api.include_router(speech_router)
    api.include_router(knowledge_agent_router)
    api.include_router(knowledge_bases_router)
    api.include_router(voice_capture_router)
    api.include_router(document_uploads_router)
    api.include_router(knowledge_management_router)

    @api.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return api


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="0.0.0.0", port=8005, reload=True)

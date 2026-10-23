"""FastAPI entry point for the knowledge agent service."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI

from backend.api.document_uploads import router as document_uploads_router
from backend.api.knowledge_agent import KnowledgeAgentPool
from backend.api.knowledge_agent import router as knowledge_agent_router
from backend.api.knowledge_bases import KnowledgeBaseCatalog
from backend.api.knowledge_bases import router as knowledge_bases_router
from backend.api.knowledge_management import router as knowledge_management_router
from backend.api.voice_capture import VoiceCaptureService
from backend.api.voice_capture import router as voice_capture_router
from backend.knowledge_bases import KNOWLEDGE_BASES
from backend.src.storage.document_uploads import DocumentUploadStore
from backend.src.services.knowledge_management import KnowledgeManagementService


def create_app(
    *,
    knowledge_agents: Mapping[str, Any] | None = None,
    voice_capture: VoiceCaptureService | None = None,
    kb_catalog: KnowledgeBaseCatalog | None = None,
    document_uploads: DocumentUploadStore | None = None,
    knowledge_management: KnowledgeManagementService | None = None,
) -> FastAPI:
    """Build the API; injected agents and services keep HTTP tests dependency-free."""

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
            try:
                yield
            finally:
                await manager.aclose()
            return

        load_dotenv()
        app.state.document_uploads = document_uploads or DocumentUploadStore()
        # Each knowledge base's store and agent open on its first request.
        pool = KnowledgeAgentPool(KNOWLEDGE_BASES)
        capture = VoiceCaptureService(KNOWLEDGE_BASES)
        manager = knowledge_management or KnowledgeManagementService(
            KNOWLEDGE_BASES, app.state.document_uploads, capture,
        )
        app.state.knowledge_management = manager
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

    api = FastAPI(
        title="Knowledge Agent Service",
        version="1.0.0",
        lifespan=lifespan,
    )
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

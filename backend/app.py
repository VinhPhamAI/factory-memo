"""FastAPI entry point for the knowledge agent service."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI

from backend.api.knowledge_agent import KnowledgeAgentPool
from backend.api.knowledge_agent import router as knowledge_agent_router
from backend.api.voice_capture import VoiceCaptureService
from backend.api.voice_capture import router as voice_capture_router
from backend.knowledge_bases import KNOWLEDGE_BASES


def create_app(
    *,
    knowledge_agents: Mapping[str, Any] | None = None,
    voice_capture: VoiceCaptureService | None = None,
) -> FastAPI:
    """Build the API; injected agents and services keep HTTP tests dependency-free."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if knowledge_agents is not None or voice_capture is not None:
            app.state.knowledge_agents = KnowledgeAgentPool({}, agents=knowledge_agents or {})
            app.state.voice_capture = voice_capture or VoiceCaptureService({})
            yield
            return

        load_dotenv()
        # Each knowledge base's store and agent open on its first request.
        pool = KnowledgeAgentPool(KNOWLEDGE_BASES)
        capture = VoiceCaptureService(KNOWLEDGE_BASES)
        app.state.knowledge_agents = pool
        app.state.voice_capture = capture
        try:
            yield
        finally:
            await capture.aclose()
            await pool.aclose()

    api = FastAPI(
        title="Knowledge Agent Service",
        version="1.0.0",
        lifespan=lifespan,
    )
    api.include_router(knowledge_agent_router)
    api.include_router(voice_capture_router)

    @api.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return api


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="0.0.0.0", port=8005, reload=True)

"""FastAPI entry point for the knowledge agent service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI

from backend.api.knowledge_agent import router as knowledge_agent_router
from backend.src.agents.agent import KnowledgeAgentService
from backend.src.storage import KnowledgeStore


def create_app(*, knowledge_agent: Any | None = None) -> FastAPI:
    """Build the API; an injected agent keeps HTTP tests dependency-free."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if knowledge_agent is not None:
            app.state.knowledge_agent = knowledge_agent
            yield
            return

        load_dotenv()
        store = KnowledgeStore.from_env()
        await store.init_schema()
        app.state.knowledge_store = store
        app.state.knowledge_agent = KnowledgeAgentService.from_env(store)
        try:
            yield
        finally:
            await store.aclose()

    api = FastAPI(
        title="Knowledge Agent Service",
        version="1.0.0",
        lifespan=lifespan,
    )
    api.include_router(knowledge_agent_router)

    @api.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return api


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="0.0.0.0", port=8005, reload=True)

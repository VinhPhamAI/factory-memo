"""Build the HTTP application and own provider/checkpoint resource lifetimes."""

# External clients are imported only inside the resource-owning lifespan.
# pylint: disable=import-outside-toplevel

from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from denso_agent.api.body_limit import BodyLimitMiddleware
from denso_agent.api.routes import router
from denso_agent.config import RuntimeSettings
from denso_agent.runtime.conversation import AgentRuntime


def create_app(
    *, settings: RuntimeSettings | None = None, runtime: AgentRuntime | None = None
) -> FastAPI:
    """Create routes immediately and open external resources only during lifespan.

    Args:
        settings: Optional validated configuration; production reads the environment.
        runtime: Optional test-owned runtime; production builds its own resources.
    Returns:
        The same FastAPI application used by native and container commands.
    """

    @asynccontextmanager
    async def lifespan(application):
        """Open resources once and close them on shutdown or partial startup failure."""
        if runtime is not None:
            yield
            return
        # Lazy imports preserve credential-free application imports and CLI help.
        from denso_agent.clients.checkpoints import (
            checkpoint_store,
            check_readiness,
        )  # pylint: disable=import-outside-toplevel
        from denso_agent.clients.modelark import (
            ModelArk,
        )  # pylint: disable=import-outside-toplevel
        from denso_agent.clients.inquiries import InquiryClient
        from denso_agent.clients.knowledge import KnowledgeClient
        from denso_agent.runtime.graph import (
            build_graph,
        )  # pylint: disable=import-outside-toplevel

        resolved = settings or RuntimeSettings()
        application.state.settings = resolved
        async with AsyncExitStack() as stack:
            provider = await stack.enter_async_context(ModelArk(resolved))
            saver = await stack.enter_async_context(
                checkpoint_store(resolved.database_url)
            )
            knowledge = inquiries = None
            if resolved.kb_backend_url:
                http = await stack.enter_async_context(
                    httpx.AsyncClient(base_url=resolved.kb_backend_url, trust_env=False)
                )
                knowledge = KnowledgeClient(
                    http,
                    timeout=resolved.search_timeout,
                    limit=resolved.search_max_results,
                    excerpt_length=resolved.search_max_excerpt_characters,
                )
                inquiries = InquiryClient(http, timeout=resolved.search_timeout)

            async def probe():
                """Check checkpoint readiness through the existing runtime pool."""
                return await check_readiness(saver)

            application.state.runtime = AgentRuntime(
                build_graph(
                    provider,
                    saver,
                    resolved,
                    knowledge=knowledge,
                    inquiries=inquiries,
                ),
                probe,
                resolved,
                provider,
            )
            try:
                yield
            finally:
                application.state.runtime = None

    application = FastAPI(title="DENSO agent", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.runtime = runtime
    application.add_middleware(BodyLimitMiddleware)
    application.include_router(router)

    @application.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException):
        """Return the stable HTTP error envelope without framework internals."""
        return JSONResponse({"error": error.detail}, status_code=error.status_code)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError):
        """Hide raw input values while reporting contract validation failure."""
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "Request fields are invalid.",
                }
            },
            status_code=422,
        )

    return application

"""ai-services-api: one FastAPI app and port in front of every AI service.

Each AI service runs in its own worker container, because their runtimes
conflict (VieGem-ASR needs Python 3.12 with PyTorch and cuDNN 9.10;
PaddleOCR-VL needs Python 3.10 with PaddlePaddle and cuDNN 9.5). This app
forwards each request, HTTP or WebSocket, to the worker that owns its path
(see `services.yaml`), so clients use one base URL.

Gateway endpoints:
- `GET /health`: the gateway is up.
- `GET /ready`: every enabled service is ready (200), or which are not (503).
- `GET /v1/services`: the services, their routes and readiness.
- `GET /v1/services/{name}/ready`: one worker's own readiness response.
- `GET /v1/models`: the models of all reachable services.
- `GET /docs`, `GET /redoc`, `GET /openapi.json`: the API of the gateway and of
  every service's public routes, merged from the workers' OpenAPI documents.

Everything else goes to the service whose route matches the path, or 404.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

from .openapi import merge
from .proxy import forward_http, forward_websocket
from .registry import Service, find, load_services

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "services.yaml"
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
TITLE = "AI services API"
VERSION = "0.2.0"
DESCRIPTION = """\
Self-hosted AI services behind one port. Each tag below is one service; the
gateway forwards its routes to the service's worker unchanged.

WebSocket endpoints are not shown here (OpenAPI cannot describe them):
`/v1/audio/transcriptions/realtime` streams live transcription. The full
reference, with the WebSocket protocol, limits and examples, is
`deployments/ai-services-api/AI_SERVICES_API_DOCS.md`.
"""


@dataclass(frozen=True)
class Settings:
    config: str = str(DEFAULT_CONFIG)
    # Longest a worker may take to answer a request (a long PDF or recording).
    request_timeout_s: float = 900.0
    ready_timeout_s: float = 5.0

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        env = os.environ.get
        return cls(
            config=env("AI_SERVICES_CONFIG") or d.config,
            request_timeout_s=float(env("AI_REQUEST_TIMEOUT_S") or d.request_timeout_s),
            ready_timeout_s=float(env("AI_READY_TIMEOUT_S") or d.ready_timeout_s),
        )


def create_app(services: list[Service] | None = None, settings: Settings | None = None) -> FastAPI:
    """Builds the gateway; without `services`, they are loaded from the registry file."""

    settings = settings or Settings.from_env()
    services = services if services is not None else load_services(settings.config, os.environ)
    by_name = {service.name: service for service in services}
    clients: dict[str, httpx.AsyncClient] = {}
    # Last OpenAPI document fetched from each worker, kept while it is down.
    worker_specs: dict[str, dict[str, Any]] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        clients["proxy"] = httpx.AsyncClient(timeout=httpx.Timeout(
            settings.request_timeout_s, connect=10.0))
        clients["probe"] = httpx.AsyncClient(timeout=settings.ready_timeout_s)
        logger.info("AI services: %s", ", ".join(
            f"{s.name} -> {s.url} {list(s.routes)}" for s in services) or "none")
        try:
            yield
        finally:
            for client in clients.values():
                await client.aclose()

    # /docs, /redoc and /openapi.json are served below from the merged document.
    app = FastAPI(title=TITLE, version=VERSION, lifespan=lifespan,
                  openapi_url=None, docs_url=None, redoc_url=None)

    async def probe(service: Service, path: str) -> tuple[int | None, Any, str | None]:
        """Status code, JSON body and error of a GET to a worker."""

        try:
            response = await clients["probe"].get(f"{service.url}{path}")
        except httpx.HTTPError as exc:
            return None, None, f"{type(exc).__name__}: {exc}"
        try:
            body = response.json()
        except ValueError:
            body = response.text[:300]
        if body in ("", None):
            # Some workers (TEI's /health) answer with an empty body.
            body = {"status": "ready" if response.status_code == 200 else "not_ready"}
        return response.status_code, body, None

    async def readiness() -> dict[str, dict[str, Any]]:
        results = await asyncio.gather(*(probe(s, s.ready_path) for s in services))
        report = {}
        for service, (status, body, error) in zip(services, results):
            entry: dict[str, Any] = {"ready": status == 200}
            if body is not None:
                entry["detail"] = body
            if error:
                entry["error"] = error
            report[service.name] = entry
        return report

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_document() -> JSONResponse:
        results = await asyncio.gather(*(probe(s, s.openapi_path) for s in services))
        missing = []
        for service, (status, body, _) in zip(services, results):
            if status == 200 and isinstance(body, dict) and isinstance(body.get("paths"), dict):
                worker_specs[service.name] = body
            elif service.name not in worker_specs:
                missing.append(service.name)
        description = DESCRIPTION
        if missing:
            description += ("\n**Not shown (worker unreachable):** "
                            + ", ".join(f"`{name}`" for name in missing) + "\n")
        tags = [{"name": "gateway", "description": "Status of the gateway and the services"}]
        tags += [{"name": s.name, "description": s.description} for s in services]
        base = get_openapi(title=TITLE, version=VERSION, description=description,
                           routes=app.routes, tags=tags)
        document = merge(base, [(s, worker_specs[s.name]) for s in services
                                if s.name in worker_specs])
        return JSONResponse(document)

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{TITLE} - Swagger UI")

    @app.get("/redoc", include_in_schema=False)
    async def redoc() -> HTMLResponse:
        return get_redoc_html(openapi_url="/openapi.json", title=f"{TITLE} - ReDoc")

    @app.get("/health", tags=["gateway"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["gateway"])
    async def ready():
        report = await readiness()
        all_ready = bool(report) and all(entry["ready"] for entry in report.values())
        return JSONResponse({"status": "ready" if all_ready else "not_ready", "services": report},
                            status_code=200 if all_ready else 503)

    @app.get("/v1/services", tags=["gateway"])
    async def list_services() -> dict[str, Any]:
        report = await readiness()
        return {"services": [{**s.to_json(), **report[s.name]} for s in services]}

    @app.get("/v1/services/{name}/ready", tags=["gateway"])
    async def service_ready(name: str):
        service = by_name.get(name)
        if service is None:
            return JSONResponse({"detail": f"unknown AI service {name!r}"}, status_code=404)
        status, body, error = await probe(service, service.ready_path)
        if status is None:
            return JSONResponse({"status": "unavailable", "error": error}, status_code=503)
        return JSONResponse(body, status_code=status)

    @app.get("/v1/models", tags=["gateway"])
    async def models() -> dict[str, Any]:
        results = await asyncio.gather(*(probe(s, s.models_path) for s in services))
        data, unavailable = [], []
        for service, (status, body, _) in zip(services, results):
            if status == 200 and isinstance(body, dict):
                data += [{**model, "service": service.name} for model in _models(body)]
            else:
                unavailable.append(service.name)
        return {"object": "list", "data": data, "unavailable_services": unavailable}

    @app.websocket("/{path:path}")
    async def websocket_route(websocket: WebSocket, path: str) -> None:
        service = find(services, websocket.url.path)
        if service is None:
            await websocket.close(1008, f"no AI service serves {websocket.url.path}")
            return
        await forward_websocket(websocket, service)

    @app.api_route("/{path:path}", methods=METHODS, include_in_schema=False)
    async def http_route(request: Request, path: str):
        service = find(services, request.url.path)
        if service is None:
            return JSONResponse({"detail": f"no AI service serves {request.url.path}"},
                                status_code=404)
        return await forward_http(clients["proxy"], service, request)

    return app


def _models(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Models from an OpenAI-style list or a TEI-style `/info` object."""

    if isinstance(body.get("data"), list):
        return [model for model in body["data"] if isinstance(model, dict)]
    if body.get("model_id"):
        model = {"id": body["model_id"], "object": "model"}
        for key in ("model_type", "model_dtype", "max_input_length", "max_batch_tokens",
                    "max_client_batch_size", "version"):
            if key in body:
                model[key] = body[key]
        return [model]
    return []


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.getenv("AI_LOG_LEVEL", "INFO").upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(create_app(), host=os.getenv("AI_HOST", "0.0.0.0"),
                port=int(os.getenv("AI_PORT", "8080")))


if __name__ == "__main__":
    main()

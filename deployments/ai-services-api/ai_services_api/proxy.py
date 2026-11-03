"""Forwarding of HTTP requests and WebSocket sessions to a service's worker.

Bodies are streamed in both directions, so large uploads and responses are not
held in memory. Hop-by-hop headers are dropped; everything else, including
status codes and error bodies, passes through unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import httpx
from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .registry import Service

logger = logging.getLogger(__name__)

HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailer", "trailers", "transfer-encoding", "upgrade", "host",
})
# Close codes that may not be sent in a close frame; replaced when relayed.
_RESERVED_CLOSE_CODES = {1004, 1005, 1006, 1015}
# WebSocket close code for "try again later".
CLOSE_UNAVAILABLE = 1013


def _target(service: Service, path: str, query: str) -> str:
    return f"{service.url}{service.target(path)}" + (f"?{query}" if query else "")


async def forward_http(client: httpx.AsyncClient, service: Service, request: Request) -> Response:
    """Sends `request` to the service and streams its response back.

    Returns:
        The worker's response, or 503 when the worker cannot be reached, 504
        when it does not answer in time and 502 for other transport errors.
    """

    headers = [(k, v) for k, v in request.headers.raw if k.decode("latin-1").lower() not in HOP_BY_HOP]
    has_body = "content-length" in request.headers or "transfer-encoding" in request.headers
    upstream = client.build_request(
        request.method, _target(service, request.url.path, request.url.query),
        headers=headers, content=request.stream() if has_body else None,
    )
    try:
        response = await client.send(upstream, stream=True)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        return _error(503, f"AI service '{service.name}' is unavailable: {type(exc).__name__}")
    except httpx.TimeoutException:
        return _error(504, f"AI service '{service.name}' did not answer in time")
    except httpx.HTTPError as exc:
        return _error(502, f"AI service '{service.name}' failed: {type(exc).__name__}: {exc}")
    out_headers = {k: v for k, v in response.headers.items() if k.lower() not in HOP_BY_HOP}
    out_headers["x-ai-service"] = service.name
    return StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                             headers=out_headers, background=BackgroundTask(response.aclose))


async def forward_websocket(websocket: WebSocket, service: Service) -> None:
    """Relays a WebSocket session between the client and the service.

    Text and binary messages pass through in both directions until either side
    closes; the other side is then closed with the same code. If the worker
    cannot be reached, the client is accepted and closed with code 1013.
    """

    url = _target(service, websocket.url.path, websocket.url.query)
    url = "ws" + url.removeprefix("http")
    try:
        # The worker pings this connection; the gateway does not ping it.
        upstream = await connect(url, max_size=None, open_timeout=10, ping_interval=None)
    except (OSError, TimeoutError, ConnectionClosed) as exc:
        await websocket.accept()
        await websocket.close(CLOSE_UNAVAILABLE, f"AI service '{service.name}' is unavailable")
        logger.warning("WebSocket to %s failed: %s", service.name, exc)
        return
    await websocket.accept()

    async def client_to_service() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                await upstream.send(message["bytes"])
            elif message.get("text") is not None:
                await upstream.send(message["text"])

    async def service_to_client() -> None:
        with suppress(ConnectionClosed):
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

    tasks = [asyncio.create_task(client_to_service()), asyncio.create_task(service_to_client())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task.exception() and not isinstance(
                    task.exception(), (WebSocketDisconnect, ConnectionClosed)):
                logger.warning("WebSocket relay to %s failed: %r", service.name, task.exception())
    finally:
        await upstream.close()
        code = upstream.close_code or 1000
        if code in _RESERVED_CLOSE_CODES:
            code = 1011
        with suppress(Exception):
            await websocket.close(code, upstream.close_reason or "")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)

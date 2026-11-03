"""Bound raw request bodies before framework JSON decoding."""

from starlette.responses import JSONResponse

# Room for one 3 MiB photo in base64 plus the message.
MAX_BODY_BYTES = 5 * 1024 * 1024


class BodyLimitMiddleware:
    """Buffer at most the configured body limit for the generation route."""

    def __init__(self, app):
        """Wrap the downstream ASGI application.

        Args:
            app: Application receiving validated-size request bodies.
        """
        self.app = app

    async def __call__(self, scope, receive, send):
        """Reject declared or chunked bodies exceeding 5 MiB.

        Args:
            scope: ASGI connection metadata.
            receive: Incoming request-message reader.
            send: Outgoing response-message writer.
        """
        if (
            scope["type"] != "http"
            or scope["path"] != "/v1/agent/stream"
            or scope["method"] != "POST"
        ):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        oversized = JSONResponse(
            {
                "error": {
                    "code": "payload_too_large",
                    "message": "Request body exceeds 5 MiB.",
                }
            },
            status_code=413,
        )
        headers = dict(scope.get("headers", []))
        declared_length = headers.get(b"content-length", b"")
        canonical_length = declared_length.lstrip(b"0") or b"0"
        body_limit = str(MAX_BODY_BYTES).encode("ascii")
        if declared_length.isdigit() and (
            len(canonical_length) > len(body_limit)
            or (
                len(canonical_length) == len(body_limit)
                and canonical_length > body_limit
            )
        ):
            await oversized(scope, receive, send)
            return
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                await oversized(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            """Replay the bounded body once, then forward disconnect monitoring."""
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)

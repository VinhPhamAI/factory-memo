"""Release admission even when transport fails before stream iteration starts."""

from contextlib import aclosing

from starlette.responses import StreamingResponse


class AgentStreamResponse(StreamingResponse):
    """Own both the body iterator and request reservation until response exit."""

    def __init__(self, content, admission, conversation_id: str):
        """Attach explicit response-lifetime cleanup to an admitted stream.

        Args:
            content: Async generator yielding complete SSE frames.
            admission: Process-local active-conversation tracker.
            conversation_id: Reservation to release on every response exit.
        """
        super().__init__(
            content,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        self._admission = admission
        self._conversation_id = conversation_id
        self._reservation = admission.reservation(conversation_id)

    async def __call__(self, scope, receive, send):
        """Close generation and release admission after normal or failed transport.

        Args:
            scope: ASGI connection metadata.
            receive: Request/disconnect reader.
            send: Response writer that may fail before the first body event.
        """
        try:
            async with aclosing(self.body_iterator):
                await super().__call__(scope, receive, send)
        finally:
            self._admission.release(self._conversation_id, self._reservation)

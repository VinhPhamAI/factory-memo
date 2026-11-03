"""HTTP client of the standalone agent service (`agent/`).

The backend authorizes the user and the conversation first, then calls the
agent with the shared service token. The token never reaches the browser.
See agent/docs/api.md for the agent contract.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError


logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5.0
HISTORY_TIMEOUT_SECONDS = 15.0
# Longest silence between stream chunks; the agent's own request deadline
# (AGENT_REQUEST_TIMEOUT_SECONDS, 120 s by default) ends a turn first.
STREAM_READ_TIMEOUT_SECONDS = 180.0


class AgentUnavailable(RuntimeError):
    """The agent could not be reached or answered unexpectedly."""


class AgentRejected(RuntimeError):
    """The agent refused a stream before it started (busy or at capacity)."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class HistoryMessage(BaseModel):
    """One saved user input or completed assistant answer."""

    role: Literal["user", "assistant"]
    text: str


class AgentHistory(BaseModel):
    """Saved public messages of a conversation, oldest first."""

    conversation_id: UUID
    messages: list[HistoryMessage]
    truncated: bool


@dataclass(frozen=True)
class AgentSettings:
    """Where the agent listens and the service token it requires."""

    base_url: str
    api_token: str

    @classmethod
    def from_env(cls) -> AgentSettings | None:
        """Read AGENT_BASE_URL and AGENT_API_TOKEN; None when either is unset."""

        base_url = os.getenv("AGENT_BASE_URL", "").strip().rstrip("/")
        token = os.getenv("AGENT_API_TOKEN", "")
        if not base_url or not token.strip():
            return None
        return cls(base_url=base_url, api_token=token)


class AgentStream:
    """An open upstream SSE response; the caller must close it."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield response bytes as they arrive, without waiting for whole events."""

        try:
            async for chunk in self._response.aiter_raw():
                yield chunk
        except httpx.HTTPError as exc:
            # The browser sees the stream end without a final event.
            logger.warning("Agent stream ended early: %s", type(exc).__name__)

    async def aclose(self) -> None:
        """Close the upstream response, which stops generation at the agent."""

        await self._response.aclose()


class AgentClient:
    """Calls the agent's history and stream endpoints over one pooled client."""

    def __init__(self, settings: AgentSettings, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(HISTORY_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        )
        self._headers = {"Authorization": f"Bearer {settings.api_token}"}

    async def aclose(self) -> None:
        """Close pooled connections."""

        await self._http.aclose()

    async def fetch_history(self, conversation_id: UUID) -> AgentHistory:
        """Fetch the saved messages of a conversation the caller already authorized.

        Raises:
            AgentUnavailable: The agent is unreachable, failed, or returned an
                invalid response.
        """

        try:
            response = await self._http.get(
                f"/v1/agent/conversations/{conversation_id}/history", headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise AgentUnavailable(type(exc).__name__) from exc
        if response.status_code != 200:
            raise AgentUnavailable(f"history returned HTTP {response.status_code}")
        try:
            return AgentHistory.model_validate_json(response.content)
        except ValidationError as exc:
            raise AgentUnavailable("history response is invalid") from exc

    async def open_stream(self, conversation_id: UUID, message: str) -> AgentStream:
        """Start an answer stream for a conversation the caller already authorized.

        Returns:
            The open stream; its status was 200.

        Raises:
            AgentRejected: HTTP 409 (conversation busy) or 429 (capacity full).
            AgentUnavailable: Any other failure before streaming started.
        """

        request = self._http.build_request(
            "POST",
            "/v1/agent/stream",
            json={"conversation_id": str(conversation_id), "message": message},
            headers=self._headers,
            timeout=httpx.Timeout(STREAM_READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        )
        try:
            response = await self._http.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise AgentUnavailable(type(exc).__name__) from exc
        if response.status_code == 200:
            return AgentStream(response)
        try:
            await response.aread()
            detail = response.json().get("error", {}) if response.status_code in (409, 429) else {}
        except (httpx.HTTPError, ValueError, AttributeError):
            detail = {}
        finally:
            await response.aclose()
        if response.status_code in (409, 429) and isinstance(detail, dict):
            raise AgentRejected(
                response.status_code,
                str(detail.get("code", "rejected")),
                str(detail.get("message", "The agent cannot accept this request now.")),
            )
        raise AgentUnavailable(f"stream returned HTTP {response.status_code}")

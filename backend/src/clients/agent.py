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
from pydantic import BaseModel, Field, JsonValue, SecretStr, ValidationError


logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5.0
HISTORY_TIMEOUT_SECONDS = 15.0
# One model call; the agent's own deadline (120 s by default) ends it first.
DRAFT_TIMEOUT_SECONDS = 150.0
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


class SourceReference(BaseModel):
    """A saved link to a knowledge-base source and optional evidence location."""

    kb: str
    source_id: UUID
    source_kind: Literal["upload", "conversation"]
    title: str
    reference: dict[str, JsonValue] | None = None


class Citation(BaseModel):
    """Evidence that a marker such as [S1] in a saved answer refers to."""

    marker: str
    evidence_id: str
    title: str
    excerpt: str
    location: str | None = None
    sources: list[SourceReference] = Field(default_factory=list, max_length=2, exclude_if=lambda sources: not sources)


class CatalogEntry(BaseModel):
    """Public metadata of one knowledge base returned by discovery."""

    name: str
    title: str
    description: str | None = None


class ToolResult(BaseModel):
    """A ranked search hit in the public tool summary."""

    rank: int = Field(ge=1)
    chunk_id: UUID
    title: str
    status: Literal["draft", "approved"]
    excerpt: str | None = None
    sources: list[SourceReference] = Field(default_factory=list, max_length=2)


class ToolSummary(BaseModel):
    """Public details of an executed knowledge or inquiry tool, without raw provider data.

    Inquiry tools fill only `inquiry_id` and `title`; the agent never sends draft text.
    """

    id: str
    name: Literal[
        "list_knowledge_bases",
        "search_knowledge",
        "create_inquiry_draft",
        "update_inquiry_draft",
    ]
    state: Literal["running", "results", "empty", "unavailable", "needs_choice", "not_draft"]
    kb: str | None = None
    query: str | None = None
    count: int | None = Field(default=None, ge=0)
    truncated: bool = False
    catalog: list[CatalogEntry] = Field(default_factory=list, max_length=50)
    results: list[ToolResult] = Field(default_factory=list)
    inquiry_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    title: str | None = Field(default=None, max_length=200, exclude_if=lambda value: value is None)


class AttachmentRef(BaseModel):
    """Reference to a photo saved with a user message (never its bytes)."""

    file_id: str
    media_type: str


class HistoryMessage(BaseModel):
    """One saved user input or completed assistant answer with its citations."""

    role: Literal["user", "assistant"]
    text: str
    citations: list[Citation] = []
    attachment: AttachmentRef | None = None
    reply_id: UUID | None = Field(default=None, exclude_if=lambda reply_id: reply_id is None)
    tools: list[ToolSummary] = Field(default_factory=list, max_length=2, exclude_if=lambda tools: not tools)


class AgentHistory(BaseModel):
    """Saved public messages of a conversation, oldest first."""

    conversation_id: UUID
    messages: list[HistoryMessage]
    truncated: bool


class DraftFields(BaseModel):
    """Model-written text of an inquiry draft."""

    title: str
    situation: str
    observations: str
    attempted_actions: str
    proposed_fix: str


class AgentInquiryDraft(BaseModel):
    """The agent's draft: text fields, cited evidence and photo references."""

    fields: DraftFields
    citations: list[Citation] = []
    attachments: list[AttachmentRef] = []


@dataclass(frozen=True)
class AgentSettings:
    """Where the agent listens and the service token it requires."""

    base_url: str
    api_token: str
    kb_tools_enabled: bool = False

    @classmethod
    def from_env(cls) -> AgentSettings | None:
        """Read the agent endpoint, service token, and optional tool toggle."""

        base_url = os.getenv("AGENT_BASE_URL", "").strip().rstrip("/")
        token = os.getenv("AGENT_API_TOKEN", "")
        if not base_url or not token.strip():
            return None
        kb_tools_enabled = os.getenv("AGENT_KB_TOOLS_ENABLED", "").strip().lower() == "true"
        return cls(base_url=base_url, api_token=token, kb_tools_enabled=kb_tools_enabled)


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
        self._kb_tools_enabled = settings.kb_tools_enabled

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

    async def create_inquiry_draft(self, conversation_id: UUID) -> AgentInquiryDraft:
        """Ask the agent to draft an inquiry from a conversation the caller authorized.

        Returns:
            The validated draft; nothing is saved by the agent.

        Raises:
            AgentRejected: HTTP 409, 422 (empty conversation), 429 or 502 (draft failed).
            AgentUnavailable: Unreachable, 503, 401, or an invalid response.
        """

        try:
            response = await self._http.post(
                f"/v1/agent/conversations/{conversation_id}/inquiry-draft",
                headers=self._headers,
                timeout=httpx.Timeout(DRAFT_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            )
        except httpx.HTTPError as exc:
            raise AgentUnavailable(type(exc).__name__) from exc
        if response.status_code == 200:
            try:
                return AgentInquiryDraft.model_validate_json(response.content)
            except ValidationError as exc:
                raise AgentUnavailable("inquiry draft response is invalid") from exc
        if response.status_code in (409, 422, 429, 502):
            try:
                detail = response.json().get("error", {})
            except (ValueError, AttributeError):
                detail = {}
            if not isinstance(detail, dict):
                detail = {}
            raise AgentRejected(
                response.status_code,
                str(detail.get("code", "rejected")),
                str(detail.get("message", "The agent could not draft this inquiry.")),
            )
        raise AgentUnavailable(f"inquiry draft returned HTTP {response.status_code}")

    async def open_stream(
        self, conversation_id: UUID, message: str, *, employee_access: SecretStr | None = None,
    ) -> AgentStream:
        """Start an answer stream for a conversation the caller already authorized.

        Args:
            conversation_id: ID of the authorized conversation.
            message: Validated user input.
            employee_access: Verified bearer access token for tool reads, if enabled.

        Returns:
            The open stream; its status was 200.

        Raises:
            AgentRejected: HTTP 409 (conversation busy) or 429 (capacity full).
            AgentUnavailable: Any other failure before streaming started.
        """

        headers = self._headers.copy()
        if self._kb_tools_enabled and employee_access is not None:
            headers["X-Employee-Access-Token"] = employee_access.get_secret_value()
        request = self._http.build_request(
            "POST",
            "/v1/agent/stream",
            json={"conversation_id": str(conversation_id), "message": message},
            headers=headers,
            timeout=httpx.Timeout(STREAM_READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        )
        try:
            response = await self._http.send(request, stream=True, follow_redirects=False)
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

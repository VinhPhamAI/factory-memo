"""Scripted incident demo, enabled by `DEMO_MODE=true`.

One prepared case is read from `scenario.json` and replayed through the
normal chat and ticket routes:

1. A chat message equal to the scripted prompt gets the scripted answer as a
   stream in the agent's SSE format (reasoning, knowledge search, text, final
   answer with citations). Any other message goes to the real agent.
2. "Tạo ticket" on that conversation saves the scripted ticket text.
3. The ticket's AI proposal is the scripted one, returned after a fixed wait.
4. A mock expert account exists so the ticket can be sent for review.

The scripted evidence is synthetic: it is not read from a knowledge base and
has no source links. Scripted conversations are kept in process memory, so
they are lost on restart and are not shared between worker processes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import unicodedata
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.clients.agent import (
    AgentHistory,
    AgentInquiryDraft,
    AgentUnavailable,
    Citation,
    DraftFields,
    HistoryMessage,
    ToolResult,
    ToolSummary,
)
from backend.src.storage.app_database import app_transaction
from backend.src.storage.orm.inquiries import Conversation, Inquiry
from backend.src.storage.orm.users import User, UserRole, normalize_username


logger = logging.getLogger(__name__)

DEMO_MODE_ENV = "DEMO_MODE"
SCENARIO_PATH = Path(__file__).with_name("scenario.json")
_ENABLED = {"1", "true", "yes", "on"}
# Shortest chat title treated as the start of the scripted prompt on reset.
_MIN_TITLE_MATCH = 20
# A word with the whitespace after it: the unit of one streamed chunk.
_WORD = re.compile(r"\S+\s*")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoTiming(_Model):
    """Waits that make the scripted steps look like real work."""

    search_seconds: float = Field(ge=0, le=30)
    chunk_delay_ms: int = Field(ge=0, le=1000)
    analysis_seconds: float = Field(ge=0, le=30)


class DemoSearchResult(_Model):
    """One scripted knowledge hit; it is both a search result and a citation."""

    chunk_id: UUID
    status: Literal["draft", "approved"]
    title: str
    location: str
    excerpt: str


class DemoSearch(_Model):
    """The scripted `search_knowledge` call; result N is cited as `[SN]`."""

    kb: str
    query: str
    results: list[DemoSearchResult] = Field(min_length=1, max_length=20)


class DemoExpert(_Model):
    """The mock expert account a scripted ticket can be sent to."""

    username: str
    display_name: str


class DemoScenario(_Model):
    """The prepared case: prompt, answer, ticket text, AI proposal and expert."""

    prompt: str = Field(min_length=1)
    timing: DemoTiming
    reasoning: str
    search: DemoSearch
    answer: str = Field(min_length=1)
    ticket: DraftFields
    proposal: str = Field(min_length=1, max_length=3500)
    expert: DemoExpert

    def matches_prompt(self, message: str) -> bool:
        """Return whether `message` is the scripted prompt, ignoring case and spacing."""

        return _normalize(message) == _normalize(self.prompt)

    def matches_ticket(self, title: str) -> bool:
        """Return whether a ticket title is the scripted ticket's title."""

        return _normalize(title) == _normalize(self.ticket.title)

    def citations(self) -> list[Citation]:
        """Return the scripted hits as answer citations `S1`, `S2`, … in rank order."""

        return [
            Citation(
                marker=f"S{rank}",
                evidence_id=str(hit.chunk_id),
                title=hit.title,
                excerpt=hit.excerpt,
                location=hit.location,
            )
            for rank, hit in enumerate(self.search.results, start=1)
        ]

    def tool(self, state: Literal["running", "results"]) -> ToolSummary:
        """Return the scripted search call, before (`running`) or with its results."""

        done = state == "results"
        return ToolSummary(
            id="demo-search",
            name="search_knowledge",
            state=state,
            kb=self.search.kb,
            query=self.search.query,
            count=len(self.search.results) if done else None,
            results=[
                ToolResult(
                    rank=rank, chunk_id=hit.chunk_id, title=hit.title,
                    status=hit.status, excerpt=hit.excerpt,
                )
                for rank, hit in enumerate(self.search.results, start=1)
            ]
            if done
            else [],
        )


def _normalize(text: str) -> str:
    """Return `text` in one Unicode form, lower case, with single spaces."""

    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


@lru_cache(maxsize=1)
def read_scenario() -> DemoScenario:
    """Read and validate the scenario file, whatever `DEMO_MODE` says; the result is cached."""

    return DemoScenario.model_validate_json(SCENARIO_PATH.read_text(encoding="utf-8"))


def load_demo_scenario() -> DemoScenario | None:
    """Return the scripted case when `DEMO_MODE` is on, otherwise None.

    Raises:
        OSError: The scenario file cannot be read.
        pydantic.ValidationError: The scenario file does not match `DemoScenario`.
    """

    if os.getenv(DEMO_MODE_ENV, "").strip().lower() not in _ENABLED:
        return None
    return read_scenario()


def scripted_proposal(title: str) -> tuple[str, float] | None:
    """Return the scripted AI proposal for a ticket and the wait before showing it.

    Args:
        title: Title of the ticket being analyzed.

    Returns:
        `(proposal text, seconds to wait)` when demo mode is on and the title is
        the scripted ticket's; None for every other ticket.
    """

    scenario = load_demo_scenario()
    if scenario is None or not scenario.matches_ticket(title):
        return None
    return scenario.proposal, scenario.timing.analysis_seconds


def _frame(name: str, payload: dict[str, Any]) -> bytes:
    """Encode one event the way the agent's `encode_event` does."""

    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


class DemoStream:
    """The scripted answer as agent SSE bytes; stands in for `AgentStream`."""

    def __init__(self, scenario: DemoScenario, conversation_id: UUID, on_complete: Any) -> None:
        self._scenario = scenario
        self._conversation_id = conversation_id
        self._on_complete = on_complete

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield status, reasoning, the search call, answer text and the final event.

        `on_complete` runs just before the final event, so a stream the browser
        stopped early leaves no saved answer, as with the real agent.
        """

        scenario = self._scenario
        delay = scenario.timing.chunk_delay_ms / 1000
        request_id = uuid4()
        sequence = 0

        def event(name: str, **fields: Any) -> bytes:
            nonlocal sequence
            sequence += 1
            return _frame(
                name,
                {
                    "request_id": str(request_id),
                    "conversation_id": str(self._conversation_id),
                    "sequence": sequence,
                    **fields,
                },
            )

        yield event("status", state="generating")
        for word in _WORD.findall(scenario.reasoning):
            await asyncio.sleep(delay)
            yield event("reasoning", delta=word)
        yield event("tool", tool=scenario.tool("running").model_dump(mode="json"))
        await asyncio.sleep(scenario.timing.search_seconds)
        tool = scenario.tool("results").model_dump(mode="json")
        yield event("tool", tool=tool)
        for word in _WORD.findall(scenario.answer):
            await asyncio.sleep(delay)
            yield event("text", delta=word)
        self._on_complete(request_id)
        yield event(
            "complete",
            finish_reason="stop",
            answer=scenario.answer,
            citations=[item.model_dump(mode="json") for item in scenario.citations()],
            search="results",
            reply_id=str(request_id),
            tools=[tool],
        )

    async def aclose(self) -> None:
        """Nothing to close: the stream holds no connection."""


class DemoAgentClient:
    """Agent client that answers the scripted prompt itself and forwards the rest.

    It has the three calls the conversation routes use. A conversation that
    received the scripted answer is remembered in memory: its history shows the
    scripted turn and its inquiry draft is the scripted ticket.
    """

    def __init__(self, inner: Any | None, scenario: DemoScenario) -> None:
        """
        Args:
            inner: The real agent client, or None when no agent is configured.
            scenario: The scripted case.
        """

        self._inner = inner
        self._scenario = scenario
        self._scripted: dict[UUID, list[HistoryMessage]] = {}

    def _agent(self) -> Any:
        if self._inner is None:
            raise AgentUnavailable("no agent is configured; demo mode answers only the scripted prompt")
        return self._inner

    async def open_stream(
        self, conversation_id: UUID, message: str, *, employee_access: SecretStr | None = None,
    ) -> Any:
        """Return the scripted stream for the scripted prompt, else the agent's stream.

        Raises:
            AgentRejected: The real agent refused the stream.
            AgentUnavailable: The message is not scripted and no agent answered.
        """

        scenario = self._scenario
        if not scenario.matches_prompt(message):
            return await self._agent().open_stream(
                conversation_id, message, employee_access=employee_access
            )

        def remember(reply_id: UUID) -> None:
            self._scripted[conversation_id] = [
                HistoryMessage(role="user", text=message),
                HistoryMessage(
                    role="assistant", text=scenario.answer, citations=scenario.citations(),
                    reply_id=reply_id, tools=[scenario.tool("results")],
                ),
            ]

        return DemoStream(scenario, conversation_id, remember)

    async def fetch_history(self, conversation_id: UUID) -> AgentHistory:
        """Return the scripted turn of a scripted conversation, then any real agent turns.

        Raises:
            AgentUnavailable: The conversation is not scripted and no agent answered.
        """

        scripted = self._scripted.get(conversation_id)
        if scripted is None:
            return await self._agent().fetch_history(conversation_id)
        later: list[HistoryMessage] = []
        if self._inner is not None:
            try:
                later = (await self._inner.fetch_history(conversation_id)).messages
            except AgentUnavailable:
                logger.warning("Demo mode: showing only the scripted turn; the agent is unavailable")
        return AgentHistory(
            conversation_id=conversation_id, messages=[*scripted, *later], truncated=False
        )

    async def create_inquiry_draft(self, conversation_id: UUID) -> AgentInquiryDraft:
        """Return the scripted ticket for a scripted conversation, else the agent's draft.

        Raises:
            AgentRejected: The real agent refused to draft.
            AgentUnavailable: The conversation is not scripted and no agent answered.
        """

        if conversation_id not in self._scripted:
            return await self._agent().create_inquiry_draft(conversation_id)
        return AgentInquiryDraft(
            fields=self._scenario.ticket, citations=self._scenario.citations()
        )


async def ensure_demo_expert(
    sessions: async_sessionmaker[AsyncSession], expert: DemoExpert
) -> None:
    """Create the mock expert account unless its username already exists.

    The account gets a random password that is not kept, so nobody can sign in
    as the mock expert. An existing account with that username is left as is.

    Raises:
        AppDatabaseUnavailable: The application database could not be reached.
    """

    username = normalize_username(expert.username)
    async with app_transaction(sessions) as session:
        if await session.scalar(select(User.id).where(User.username == username)) is not None:
            return
        password_hash = await asyncio.to_thread(PasswordHasher().hash, secrets.token_urlsafe(32))
        session.add(
            User(
                username=username, display_name=expert.display_name,
                password_hash=password_hash, role=UserRole.EXPERT.value,
            )
        )


async def reset_demo_data(
    sessions: async_sessionmaker[AsyncSession], scenario: DemoScenario
) -> tuple[int, int]:
    """Hide the tickets and chats earlier demo runs left, so the demo starts clean.

    Marks as deleted, for every user: tickets whose title is the scripted
    ticket title, the chats they were created from, and chats whose title is
    the start of the scripted prompt. Rows are kept, as when a user deletes
    them in the app. The mock expert account is not touched.

    Returns:
        `(tickets hidden, chats hidden)`.

    Raises:
        AppDatabaseUnavailable: The application database could not be reached.
    """

    prompt = " ".join(scenario.prompt.split())
    async with app_transaction(sessions) as session:
        tickets = (
            await session.execute(
                update(Inquiry)
                .where(Inquiry.title == scenario.ticket.title, Inquiry.deleted_at.is_(None))
                .values(deleted_at=func.now())
                .returning(Inquiry.conversation_id)
                .execution_options(synchronize_session=False)
            )
        ).scalars().all()
        chats = (
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.deleted_at.is_(None),
                    Conversation.id.in_(tickets)
                    | (
                        (func.length(Conversation.title) >= _MIN_TITLE_MATCH)
                        & func.starts_with(literal(prompt), Conversation.title)
                    ),
                )
                .values(deleted_at=func.now())
                .returning(Conversation.id)
                .execution_options(synchronize_session=False)
            )
        ).scalars().all()
    return len(tickets), len(chats)

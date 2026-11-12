"""Mock tickets that fill the `/tickets` board in demo mode.

`tickets.json` lists tickets in every board column and in each inquiry status,
written like real factory faults. They are saved through the application
tables like tickets made in the app: a hidden conversation, an inquiry, and
events for the expert's questions, the employee's replies and the decision.
The API seeds them at start when `DEMO_MODE` is on and none exist yet;
`make demo-reset` hides them and seeds a fresh set.

All content is synthetic. The proposals and expert decisions are not approved
procedures. Seeded conversations are hidden and have no chat history.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.services.demo.scenario import DemoExpert, ensure_mock_user
from backend.src.services.inquiries import InquiryContext
from backend.src.storage.app_database import app_transaction
from backend.src.storage.inquiries import DEMO_PROPOSAL_MARKER, TRANSITIONS
from backend.src.storage.orm import Inquiry, InquiryEvent, User, UserRole
from backend.src.storage.orm.base import new_id
from backend.src.storage.orm.inquiries import (
    EVENT_KINDS_REQUIRING_CONTENT,
    Conversation,
    InquiryEventKind,
)

TICKETS_PATH = Path(__file__).with_name("tickets.json")
# `TRANSITIONS` names the expert's relation to a ticket "assignee".
_RELATION = {"requester": "requester", "expert": "assignee"}
# Same limit as `InquiryContext.proposed_fix`.
_MAX_PROPOSED_FIX = 4000


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoAccountMissing(LookupError):
    """The demo employee or expert account does not exist yet."""


class SeedEvent(_Model):
    """One expert or employee response on a submitted mock ticket."""

    by: Literal["requester", "expert"]
    kind: Literal["accepted", "edited", "denied", "question", "reply"]
    content: str | None = Field(default=None, max_length=4000)
    hours_ago: float = Field(ge=0)


class SeedTicket(_Model):
    """A mock ticket and the history that leads to its status.

    Times are hours before seeding, so the board always shows recent activity.
    """

    requester: str
    title: str = Field(min_length=1, max_length=200)
    status: Literal["draft", "open", "needs_info", "answered", "closed"]
    context: InquiryContext
    # AI proposal block; with one, a draft shows in "In progress".
    proposal: str | None = Field(default=None, min_length=1)
    created_hours_ago: float = Field(ge=0)
    submitted_hours_ago: float | None = Field(default=None, ge=0)
    updated_hours_ago: float | None = Field(default=None, ge=0)
    events: list[SeedEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_history(self) -> SeedTicket:
        """Reject a status the stored history could not have produced."""

        if len(self.proposed_fix()) > _MAX_PROPOSED_FIX:
            raise ValueError(f"{self.title}: proposed fix and proposal exceed {_MAX_PROPOSED_FIX} characters")
        if self.status == "draft":
            if self.submitted_hours_ago is not None or self.events:
                raise ValueError(f"{self.title}: a draft has no submission or events")
            return self
        if self.submitted_hours_ago is None or self.submitted_hours_ago > self.created_hours_ago:
            raise ValueError(f"{self.title}: submitted_hours_ago must exist and not be before creation")
        status, last = "open", self.submitted_hours_ago
        for event in self.events:
            if event.hours_ago > last:
                raise ValueError(f"{self.title}: events must be in time order after submission")
            last = event.hours_ago
            if InquiryEventKind(event.kind) in EVENT_KINDS_REQUIRING_CONTENT and not (event.content or "").strip():
                raise ValueError(f"{self.title}: a {event.kind} event needs content")
            rule = TRANSITIONS.get((status, event.kind))
            if rule is None or rule[0] != _RELATION[event.by]:
                raise ValueError(f"{self.title}: {event.by} cannot record {event.kind} on a {status} ticket")
            status = rule[1]
        if status != self.status:
            raise ValueError(f"{self.title}: events lead to {status}, not {self.status}")
        if self.updated_hours_ago is not None and self.updated_hours_ago > last:
            raise ValueError(f"{self.title}: updated_hours_ago is before the last activity")
        return self

    def proposed_fix(self) -> str:
        """Return `proposed_fix` as the demo proposal endpoint stores it: own text, then the marked block."""

        own = self.context.proposed_fix
        if self.proposal is None:
            return own
        block = f"{DEMO_PROPOSAL_MARKER}\n{self.proposal}"
        return f"{own}\n\n{block}" if own else block

    def last_activity_hours_ago(self) -> float:
        """Return how long ago the ticket last changed; it sets its order on the board."""

        if self.updated_hours_ago is not None:
            return self.updated_hours_ago
        times = [self.created_hours_ago, *(e.hours_ago for e in self.events)]
        if self.submitted_hours_ago is not None:
            times.append(self.submitted_hours_ago)
        return min(times)


class TicketSeed(_Model):
    """Content of `tickets.json`: the employee account, mock colleagues and tickets."""

    requester_username: str
    employees: list[DemoExpert] = Field(default_factory=list)
    tickets: list[SeedTicket] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_references(self) -> TicketSeed:
        """Require known requesters and unique titles, which identify seeded tickets."""

        known = {self.requester_username, *(e.username for e in self.employees)}
        unknown = {t.requester for t in self.tickets} - known
        if unknown:
            raise ValueError(f"unknown requester(s): {sorted(unknown)}")
        titles = [t.title for t in self.tickets]
        if len(set(titles)) != len(titles):
            raise ValueError("ticket titles must be unique")
        return self


@lru_cache(maxsize=1)
def read_ticket_seed() -> TicketSeed:
    """Read and validate the mock ticket file; the result is cached.

    Raises:
        OSError: The file cannot be read.
        pydantic.ValidationError: The file does not match `TicketSeed`.
    """

    return TicketSeed.model_validate_json(TICKETS_PATH.read_text(encoding="utf-8"))


async def seed_demo_tickets(
    sessions: async_sessionmaker[AsyncSession],
    seed: TicketSeed,
    expert: DemoExpert,
    *,
    replace: bool = False,
    now: datetime | None = None,
) -> int:
    """Save the mock tickets so they appear on the board of the demo accounts.

    Tickets are requested by the demo employee or a mock colleague and
    assigned to the demo expert, so the employee sees the first group and the
    expert sees everything that was submitted. Mock colleagues are created when
    missing. Each ticket gets a hidden conversation to satisfy the inquiry's
    conversation rule. A ticket is identified by title and requester.

    Args:
        sessions: Application database sessions.
        seed: Mock tickets from `tickets.json`.
        expert: The demo expert the submitted tickets are assigned to.
        replace: When False, seed only if no ticket of the seed exists, hidden
            ones included, so deleting a mock ticket is not undone by a restart.
            When True, hide the visible ones first and always save a fresh set.
        now: Time the history is counted back from; the current time by default.

    Returns:
        The number of tickets saved; 0 when `replace` is False and the seed exists.

    Raises:
        DemoAccountMissing: The demo employee or expert is not in the database;
            run `make demo-accounts`.
        AppDatabaseUnavailable: The application database could not be reached.
    """

    now = now or datetime.now(UTC)

    def ago(hours: float) -> datetime:
        return now - timedelta(hours=hours)

    async with app_transaction(sessions) as session:
        people = await _accounts(session, seed, expert)
        titles = [ticket.title for ticket in seed.tickets]
        existing = (
            await session.execute(
                select(Inquiry.id, Inquiry.conversation_id, Inquiry.deleted_at).where(
                    Inquiry.title.in_(titles), Inquiry.requested_by.in_(
                        list({people[t.requester] for t in seed.tickets})
                    ),
                )
            )
        ).all()
        if existing and not replace:
            return 0
        visible = [row for row in existing if row.deleted_at is None]
        if visible:
            await session.execute(
                update(Inquiry).where(Inquiry.id.in_([row.id for row in visible]))
                .values(deleted_at=func.now()).execution_options(synchronize_session=False)
            )
            await session.execute(
                update(Conversation)
                .where(Conversation.id.in_([row.conversation_id for row in visible]))
                .values(deleted_at=func.now()).execution_options(synchronize_session=False)
            )

        conversations = {ticket.title: new_id() for ticket in seed.tickets}
        session.add_all(
            Conversation(
                id=conversations[ticket.title], owner_id=people[ticket.requester], title=ticket.title,
                created_at=ago(ticket.created_hours_ago),
                updated_at=ago(ticket.created_hours_ago), deleted_at=now,
            )
            for ticket in seed.tickets
        )
        await session.flush()
        inquiries = []
        for ticket in seed.tickets:
            submitted = ticket.submitted_hours_ago
            inquiry = Inquiry(
                id=new_id(), conversation_id=conversations[ticket.title],
                requested_by=people[ticket.requester],
                assigned_to=None if submitted is None else people["expert"],
                title=ticket.title,
                context={**ticket.context.model_dump(mode="json"), "proposed_fix": ticket.proposed_fix()},
                status=ticket.status,
                submitted_at=None if submitted is None else ago(submitted),
                created_at=ago(ticket.created_hours_ago),
                updated_at=ago(ticket.last_activity_hours_ago()),
            )
            inquiries.append((ticket, inquiry))
        session.add_all(inquiry for _, inquiry in inquiries)
        await session.flush()
        session.add_all(
            InquiryEvent(
                inquiry_id=inquiry.id,
                actor_id=people["expert"] if event.by == "expert" else people[ticket.requester],
                kind=event.kind, content=event.content, created_at=ago(event.hours_ago),
            )
            for ticket, inquiry in inquiries
            for event in ticket.events
        )
    return len(seed.tickets)


async def _accounts(session: AsyncSession, seed: TicketSeed, expert: DemoExpert) -> dict[str, UUID]:
    """Return account IDs by username, plus the key `expert`; create missing mock colleagues.

    Raises:
        DemoAccountMissing: The demo employee or expert is not in the database.
    """

    people: dict[str, UUID] = {}
    for key, username in (("requester", seed.requester_username), ("expert", expert.username)):
        found = await session.scalar(
            select(User.id).where(User.username == username, User.deleted_at.is_(None))
        )
        if found is None:
            raise DemoAccountMissing(f"{username} does not exist; run `make demo-accounts`")
        people[key] = found
    people[seed.requester_username] = people["requester"]
    for employee in seed.employees:
        people[employee.username] = await ensure_mock_user(
            session, employee.username, employee.display_name, UserRole.EMPLOYEE
        )
    return people

"""Conversation ownership, expert inquiries and their response history."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.storage.orm.base import (
    Base,
    RecordDates,
    allowed_values,
    json_object,
    uuid_primary_key,
)


class InquiryStatus(StrEnum):
    """Progress of an inquiry through expert review."""

    OPEN = "open"
    IN_REVIEW = "in_review"
    NEEDS_INFO = "needs_info"
    ANSWERED = "answered"
    CLOSED = "closed"


class InquiryEventKind(StrEnum):
    """Type of a permanent inquiry response."""

    ACCEPTED = "accepted"
    EDITED = "edited"
    DENIED = "denied"
    QUESTION = "question"
    REPLY = "reply"
    CLOSED = "closed"


# Kinds whose meaning depends on text: a revised solution, a question, a reply
# or the reason for a denial.
EVENT_KINDS_REQUIRING_CONTENT = (
    InquiryEventKind.EDITED,
    InquiryEventKind.DENIED,
    InquiryEventKind.QUESTION,
    InquiryEventKind.REPLY,
)


class Conversation(RecordDates, Base):
    """Owner and list information of an agent conversation.

    The ID is also the agent `conversation_id` / checkpoint `thread_id`;
    messages stay in the agent's own database.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        # Target of the inquiry rule "the requester owns the conversation".
        UniqueConstraint("id", "owner_id"),
        Index(None, "owner_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(Text)
    machine_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("machines.id"), index=True
    )


class Inquiry(RecordDates, Base):
    """A self-contained fault description sent to a remote expert.

    The submitted title, context, machine and requester are preserved; later
    corrections are recorded as events. Whether `assigned_to` is an enabled
    expert or admin is checked by the backend, because roles can change later.
    """

    __tablename__ = "inquiries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "requested_by"],
            ["conversations.id", "conversations.owner_id"],
        ),
        allowed_values("status", InquiryStatus),
        json_object("context"),
        Index(None, "requested_by", "updated_at", "id"),
        Index(None, "assigned_to", "updated_at", "id"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    conversation_id: Mapped[UUID] = mapped_column(index=True)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    assigned_to: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    machine_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("machines.id"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    # situation, observations, attempted_actions, proposed_fix and evidence;
    # the backend validates these fields.
    context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        Text,
        default=InquiryStatus.OPEN.value,
        server_default=InquiryStatus.OPEN.value,
    )

    events: Mapped[list[InquiryEvent]] = relationship(
        back_populates="inquiry", lazy="raise", order_by="InquiryEvent.created_at"
    )
    files: Mapped[list[InquiryFile]] = relationship(
        back_populates="inquiry", lazy="raise"
    )


class InquiryEvent(Base):
    """A permanent expert decision, question, revised solution or employee reply.

    A trigger rejects updates and deletions; corrections are new events. Save
    an event together with the new inquiry status in one transaction while
    holding a row lock on the inquiry.
    """

    __tablename__ = "inquiry_events"
    __table_args__ = (
        allowed_values("kind", InquiryEventKind),
        CheckConstraint(
            "kind NOT IN ("
            + ", ".join(f"'{kind.value}'" for kind in EVENT_KINDS_REQUIRING_CONTENT)
            + ") OR (content IS NOT NULL AND btrim(content) <> '')",
            name="content_required_for_kind",
        ),
        # Target of the inquiry_files rule "the event belongs to the same inquiry".
        UniqueConstraint("id", "inquiry_id"),
        Index(None, "inquiry_id", "created_at", "id"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    inquiry_id: Mapped[UUID] = mapped_column(ForeignKey("inquiries.id"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    inquiry: Mapped[Inquiry] = relationship(back_populates="events", lazy="raise")


class InquiryFile(Base):
    """A file attached to an inquiry at submission or with a later event.

    Each file is attached once per inquiry; later responses can refer to it.
    """

    __tablename__ = "inquiry_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "inquiry_id"],
            ["inquiry_events.id", "inquiry_events.inquiry_id"],
        ),
        Index(None, "event_id"),
    )

    inquiry_id: Mapped[UUID] = mapped_column(
        ForeignKey("inquiries.id"), primary_key=True
    )
    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("files.id"), primary_key=True, index=True
    )
    # NULL for files supplied with the original submission.
    event_id: Mapped[UUID | None]

    inquiry: Mapped[Inquiry] = relationship(
        back_populates="files", lazy="raise", foreign_keys=[inquiry_id]
    )
    # View-only: inquiry_id is written through the `inquiry` relationship.
    event: Mapped[InquiryEvent | None] = relationship(lazy="raise", viewonly=True)

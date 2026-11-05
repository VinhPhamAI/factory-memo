"""Inquiry rows, attached files and events in the application database.

Every read is scoped to the requester, or to the assignee once the inquiry is
no longer a draft. A missing, deleted or foreign inquiry therefore looks the
same: `InquiryNotFound`. Status rules for edits are checked while the inquiry
row is locked, so two concurrent requests cannot both pass the same check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from backend.src.storage.app_database import app_transaction
from backend.src.storage.auth import ACCOUNT_ELIGIBLE
from backend.src.storage.file_library import FileRecord
from backend.src.storage.orm import (
    File,
    Inquiry,
    InquiryEvent,
    InquiryFile,
    InquiryStatus,
    User,
    UserRole,
)


# The lists have no pagination yet; they return the most recently active items.
LIST_LIMIT = 200

Box = Literal["sent", "assigned"]

# Called under the row lock with the stored title, context and attached file IDs;
# raises to abort the transaction.
SubmitCheck = Callable[[str, dict[str, Any], set[UUID]], None]
# Called under the row lock with the attached file IDs.
AttachedCheck = Callable[[set[UUID]], None]

# (current status, event kind) -> (actor's relation to the inquiry, next status).
# Every other pair is rejected with InvalidTransition.
TRANSITIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("open", "accepted"): ("assignee", "answered"),
    ("open", "edited"): ("assignee", "answered"),
    ("open", "denied"): ("assignee", "closed"),
    ("open", "question"): ("assignee", "needs_info"),
    ("needs_info", "reply"): ("requester", "open"),
}


# Files one inquiry may have; matches the `file_ids` limit of a new draft.
MAX_ATTACHED_FILES = 20


class InquiryNotFound(LookupError):
    """No inquiry with this ID is visible to the user."""


class InvalidTransition(ValueError):
    """The inquiry's current status does not allow the requested action."""


class InvalidContext(ValueError):
    """The inquiry context, title or attachments are not valid for the requested action."""


class IneligibleAssignee(ValueError):
    """The chosen assignee is not an active, undeleted expert."""


@dataclass(frozen=True)
class InquirySummary:
    """List fields of an inquiry."""

    id: UUID
    title: str
    status: str
    requested_by: UUID
    requester_name: str
    assigned_to: UUID | None
    assignee_name: str | None
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None


@dataclass(frozen=True)
class InquiryFileRef:
    """An attached file; the storage location is never included."""

    file_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    event_id: UUID | None


@dataclass(frozen=True)
class InquiryEventRecord:
    """A permanent response recorded on an inquiry."""

    id: UUID
    actor_id: UUID
    actor_name: str
    kind: str
    content: str | None
    created_at: datetime


@dataclass(frozen=True)
class InquiryDetail:
    """An inquiry with its context, attached files and events."""

    summary: InquirySummary
    conversation_id: UUID
    context: dict[str, Any]
    files: list[InquiryFileRef]
    events: list[InquiryEventRecord]


def _visible(user_id: UUID) -> Any:
    """Return the access rule: the requester, or the assignee of a submitted inquiry."""

    return and_(
        Inquiry.deleted_at.is_(None),
        or_(
            Inquiry.requested_by == user_id,
            and_(Inquiry.assigned_to == user_id, Inquiry.status != InquiryStatus.DRAFT.value),
        ),
    )


def _summary_query(requester: Any, assignee: Any) -> Any:
    """Return the select of list columns joined to requester and optional assignee names."""

    return (
        select(
            Inquiry.id, Inquiry.title, Inquiry.status, Inquiry.requested_by,
            requester.display_name, Inquiry.assigned_to, assignee.display_name,
            Inquiry.created_at, Inquiry.updated_at, Inquiry.submitted_at,
        )
        .join(requester, requester.id == Inquiry.requested_by)
        .outerjoin(assignee, assignee.id == Inquiry.assigned_to)
    )


class InquiryStore:
    """Reads and writes inquiry rows through short transactions.

    Raises AppDatabaseUnavailable from every method when the database cannot
    be reached.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_draft(
        self,
        requester_id: UUID,
        conversation_id: UUID,
        title: str,
        context: dict[str, Any],
        file_ids: list[UUID],
    ) -> UUID:
        """Insert a draft and attach `file_ids` in one transaction; return its ID.

        The caller has already checked that the requester owns the conversation
        and can read each file.
        """

        async with app_transaction(self._sessions) as session:
            inquiry = Inquiry(
                conversation_id=conversation_id,
                requested_by=requester_id,
                title=title,
                context=context,
                status=InquiryStatus.DRAFT.value,
            )
            session.add(inquiry)
            await session.flush()
            session.add_all(
                InquiryFile(inquiry_id=inquiry.id, file_id=file_id) for file_id in dict.fromkeys(file_ids)
            )
        return inquiry.id

    async def list_box(self, user_id: UUID, box: Box) -> list[InquirySummary]:
        """Return the user's sent inquiries (drafts included) or assigned ones (never drafts)."""

        requester, assignee = aliased(User), aliased(User)
        if box == "sent":
            condition = Inquiry.requested_by == user_id
        else:
            condition = and_(Inquiry.assigned_to == user_id, Inquiry.status != InquiryStatus.DRAFT.value)
        async with app_transaction(self._sessions) as session:
            rows = await session.execute(
                _summary_query(requester, assignee)
                .where(condition, Inquiry.deleted_at.is_(None))
                .order_by(Inquiry.updated_at.desc(), Inquiry.id.desc())
                .limit(LIST_LIMIT)
            )
            return [InquirySummary(*row) for row in rows]

    async def get_visible(self, inquiry_id: UUID, user_id: UUID) -> InquiryDetail:
        """Return an inquiry the user may open, with files and events.

        Raises:
            InquiryNotFound: It is missing, deleted or not visible to the user.
        """

        requester, assignee = aliased(User), aliased(User)
        actor = aliased(User)
        async with app_transaction(self._sessions) as session:
            row = (
                await session.execute(
                    _summary_query(requester, assignee)
                    .add_columns(Inquiry.conversation_id, Inquiry.context)
                    .where(Inquiry.id == inquiry_id, _visible(user_id))
                )
            ).one_or_none()
            if row is None:
                raise InquiryNotFound(inquiry_id)
            files = await session.execute(
                select(File.id, File.filename, File.content_type, File.size_bytes, InquiryFile.event_id)
                .join(InquiryFile, InquiryFile.file_id == File.id)
                .where(InquiryFile.inquiry_id == inquiry_id)
                .order_by(File.created_at, File.id)
            )
            events = await session.execute(
                select(
                    InquiryEvent.id, InquiryEvent.actor_id, actor.display_name,
                    InquiryEvent.kind, InquiryEvent.content, InquiryEvent.created_at,
                )
                .join(actor, actor.id == InquiryEvent.actor_id)
                .where(InquiryEvent.inquiry_id == inquiry_id)
                .order_by(InquiryEvent.created_at, InquiryEvent.id)
            )
            return InquiryDetail(
                summary=InquirySummary(*row[:10]),
                conversation_id=row[10],
                context=row[11],
                files=[InquiryFileRef(*item) for item in files],
                events=[InquiryEventRecord(*item) for item in events],
            )

    async def update_draft(
        self,
        inquiry_id: UUID,
        requester_id: UUID,
        *,
        title: str | None,
        context: dict[str, Any] | None,
        check_attached: AttachedCheck | None = None,
    ) -> None:
        """Change the title and/or context of the requester's draft.

        Args:
            inquiry_id: The draft to change.
            requester_id: Its owner.
            title: New title, or None to keep it.
            context: New context, or None to keep it.
            check_attached: Called with the attached file IDs while the row is
                locked, when `context` is given; an exception aborts the update.

        Raises:
            InquiryNotFound: It is missing, deleted or owned by another user.
            InvalidTransition: It is no longer a draft.
        """

        async with app_transaction(self._sessions) as session:
            await self._lock_draft(session, inquiry_id, requester_id)
            if context is not None and check_attached is not None:
                check_attached(await self._attached_ids(session, inquiry_id))
            values: dict[str, Any] = {}
            if title is not None:
                values["title"] = title
            if context is not None:
                values["context"] = context
            if values:
                await session.execute(
                    update(Inquiry).where(Inquiry.id == inquiry_id).values(**values)
                    .execution_options(synchronize_session=False)
                )

    async def soft_delete_draft(self, inquiry_id: UUID, requester_id: UUID) -> None:
        """Hide the requester's draft; the row stays.

        Raises:
            InquiryNotFound: It is missing, deleted or owned by another user.
            InvalidTransition: It is no longer a draft.
        """

        async with app_transaction(self._sessions) as session:
            await self._lock_draft(session, inquiry_id, requester_id)
            await session.execute(
                update(Inquiry).where(Inquiry.id == inquiry_id).values(deleted_at=func.now())
                .execution_options(synchronize_session=False)
            )

    async def attach_file(self, inquiry_id: UUID, requester_id: UUID, file_id: UUID) -> None:
        """Attach a file to the requester's draft; attaching twice is a no-op.

        The caller has already checked that the requester can read the file.

        Raises:
            InquiryNotFound: It is missing, deleted or owned by another user.
            InvalidTransition: It is no longer a draft.
            InvalidContext: The draft already has `MAX_ATTACHED_FILES` files.
        """

        async with app_transaction(self._sessions) as session:
            await self._lock_draft(session, inquiry_id, requester_id)
            already = await session.scalar(
                select(InquiryFile.file_id).where(
                    InquiryFile.inquiry_id == inquiry_id, InquiryFile.file_id == file_id
                )
            )
            if already is None:
                attached = await session.scalar(
                    select(func.count()).where(InquiryFile.inquiry_id == inquiry_id)
                )
                if attached >= MAX_ATTACHED_FILES:
                    raise InvalidContext(f"A draft can have at most {MAX_ATTACHED_FILES} files")
                session.add(InquiryFile(inquiry_id=inquiry_id, file_id=file_id))
            await self._touch(session, inquiry_id)

    async def detach_file(self, inquiry_id: UUID, requester_id: UUID, file_id: UUID) -> None:
        """Remove a file from the requester's draft; removing an absent file is a no-op.

        Evidence items of the context that refer to the file are removed in the
        same transaction, so the context never points at a detached file.

        Raises:
            InquiryNotFound: It is missing, deleted or owned by another user.
            InvalidTransition: It is no longer a draft.
        """

        async with app_transaction(self._sessions) as session:
            _, context = await self._lock_draft(session, inquiry_id, requester_id)
            await session.execute(
                delete(InquiryFile).where(
                    InquiryFile.inquiry_id == inquiry_id, InquiryFile.file_id == file_id
                )
            )
            evidence = context.get("evidence") if isinstance(context, dict) else None
            if isinstance(evidence, list):
                kept = [
                    item for item in evidence
                    if not (isinstance(item, dict) and item.get("file_id") == str(file_id))
                ]
                if len(kept) != len(evidence):
                    await session.execute(
                        update(Inquiry).where(Inquiry.id == inquiry_id)
                        .values(context={**context, "evidence": kept})
                        .execution_options(synchronize_session=False)
                    )
            await self._touch(session, inquiry_id)

    async def submit(
        self,
        inquiry_id: UUID,
        requester_id: UUID,
        assigned_to: UUID,
        check: SubmitCheck | None = None,
    ) -> None:
        """Move the requester's draft to `open` for an eligible expert.

        The completeness check and the assignee eligibility check run in the
        same transaction as the update, on the row read after it is locked.

        Args:
            inquiry_id: The draft to submit.
            requester_id: Its owner.
            assigned_to: The expert to send it to.
            check: Called under the lock with the stored title, context and
                attached file IDs; an exception aborts the submission.

        Raises:
            InquiryNotFound: It is missing, deleted or owned by another user.
            InvalidTransition: It is no longer a draft.
            IneligibleAssignee: `assigned_to` is not an eligible expert.
        """

        async with app_transaction(self._sessions) as session:
            title, context = await self._lock_draft(session, inquiry_id, requester_id)
            if check is not None:
                check(title, context, await self._attached_ids(session, inquiry_id))
            if not await self._is_eligible_expert(session, assigned_to):
                raise IneligibleAssignee(assigned_to)
            await session.execute(
                update(Inquiry).where(Inquiry.id == inquiry_id)
                .values(
                    status=InquiryStatus.OPEN.value, assigned_to=assigned_to,
                    submitted_at=func.now(),
                )
                .execution_options(synchronize_session=False)
            )

    async def record_event(
        self, inquiry_id: UUID, user_id: UUID, kind: str, content: str | None
    ) -> None:
        """Append an event and move the status in one transaction.

        The inquiry row is locked first, so of two concurrent actions on the
        same status only one passes the `TRANSITIONS` check. The actor's
        relation (requester or assignee) is read from the row, never supplied
        by the caller.

        Raises:
            InquiryNotFound: It is missing, deleted or not visible to the user
                (an assignee never sees a draft).
            InvalidTransition: The status, event kind and relation do not match
                an entry of `TRANSITIONS`.
        """

        async with app_transaction(self._sessions) as session:
            row = (
                await session.execute(
                    select(Inquiry.status, Inquiry.requested_by, Inquiry.assigned_to)
                    .where(Inquiry.id == inquiry_id, _visible(user_id))
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise InquiryNotFound(inquiry_id)
            status, requested_by, assigned_to = row
            relations = {
                name
                for name, owner in (("requester", requested_by), ("assignee", assigned_to))
                if owner == user_id
            }
            rule = TRANSITIONS.get((status, kind))
            if rule is None or rule[0] not in relations:
                raise InvalidTransition(f"Cannot record '{kind}' on an inquiry that is {status}")
            session.add(
                InquiryEvent(
                    inquiry_id=inquiry_id, actor_id=user_id, kind=kind, content=content,
                    created_at=func.clock_timestamp(),
                )
            )
            await session.execute(
                update(Inquiry).where(Inquiry.id == inquiry_id)
                .values(status=rule[1], updated_at=func.now())
                .execution_options(synchronize_session=False)
            )

    async def eligible_experts(self) -> list[tuple[UUID, str]]:
        """Return `(id, display_name)` of active, undeleted experts by name."""

        async with app_transaction(self._sessions) as session:
            rows = await session.execute(
                select(User.id, User.display_name)
                .where(ACCOUNT_ELIGIBLE, User.role == UserRole.EXPERT.value)
                .order_by(func.lower(User.display_name), User.id)
            )
            return [(row[0], row[1]) for row in rows]

    async def get_attached_file(
        self, inquiry_id: UUID, file_id: UUID, user_id: UUID
    ) -> FileRecord:
        """Return a file attached to an inquiry the user may open.

        The file's `deleted_at` is deliberately not filtered: removing a file
        from the owner's library keeps it readable for the inquiry's people.

        Raises:
            InquiryNotFound: The inquiry is not visible or the file is not attached to it.
        """

        async with app_transaction(self._sessions) as session:
            row = (
                await session.execute(
                    select(File)
                    .join(InquiryFile, InquiryFile.file_id == File.id)
                    .join(Inquiry, Inquiry.id == InquiryFile.inquiry_id)
                    .where(Inquiry.id == inquiry_id, File.id == file_id, _visible(user_id))
                )
            ).scalar_one_or_none()
            if row is None:
                raise InquiryNotFound(inquiry_id)
            return FileRecord(
                id=row.id, filename=row.filename, content_type=row.content_type,
                size_bytes=row.size_bytes, storage_key=row.storage_key, sha256=row.sha256,
                uploaded_by=row.uploaded_by, created_at=row.created_at, deleted_at=row.deleted_at,
            )

    @staticmethod
    async def _is_eligible_expert(session: AsyncSession, user_id: UUID) -> bool:
        """Return whether the user is an active, undeleted expert."""

        found = await session.scalar(
            select(User.id).where(
                User.id == user_id, ACCOUNT_ELIGIBLE, User.role == UserRole.EXPERT.value
            )
        )
        return found is not None

    @staticmethod
    async def _attached_ids(session: AsyncSession, inquiry_id: UUID) -> set[UUID]:
        """Return the IDs of files attached to the inquiry."""

        rows = await session.scalars(
            select(InquiryFile.file_id).where(InquiryFile.inquiry_id == inquiry_id)
        )
        return set(rows)

    @staticmethod
    async def _lock_draft(
        session: AsyncSession, inquiry_id: UUID, requester_id: UUID
    ) -> tuple[str, dict[str, Any]]:
        """Lock the requester's inquiry row and require status `draft`.

        Returns:
            The title and context as stored after the lock was taken.

        Raises:
            InquiryNotFound: It is missing, deleted or owned by another user.
            InvalidTransition: It is no longer a draft.
        """

        row = (
            await session.execute(
                select(Inquiry.status, Inquiry.title, Inquiry.context)
                .where(
                    Inquiry.id == inquiry_id,
                    Inquiry.requested_by == requester_id,
                    Inquiry.deleted_at.is_(None),
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise InquiryNotFound(inquiry_id)
        status, title, context = row
        if status != InquiryStatus.DRAFT.value:
            raise InvalidTransition(f"Inquiry is {status}, not a draft")
        return title, context

    @staticmethod
    async def _touch(session: AsyncSession, inquiry_id: UUID) -> None:
        """Set the inquiry's `updated_at` to now."""

        await session.execute(
            update(Inquiry).where(Inquiry.id == inquiry_id).values(updated_at=func.now())
            .execution_options(synchronize_session=False)
        )

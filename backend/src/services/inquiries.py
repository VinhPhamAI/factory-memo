"""Rules of inquiries: drafts, submission to an expert and evidence access.

An inquiry is a self-contained fault description. The requester edits it as a
draft, then submits it to an eligible expert (an active, undeleted account with
role `expert`). Access rule: the requester always; the assignee once the
inquiry is no longer a draft. Everyone else gets `InquiryNotFound`.
Inquiries are not knowledge: nothing here writes knowledge records.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.src.clients.agent import AgentInquiryDraft
from backend.src.services.file_library import FileLibraryService, FileNotFound
from backend.src.storage.auth import Account
from backend.src.storage.conversations import ConversationNotFound, ConversationStore
from backend.src.storage.file_library import FileRecord
from backend.src.storage.inquiries import (
    Box,
    IneligibleAssignee,
    InquiryDetail,
    InquiryNotFound,
    InquiryStore,
    InquirySummary,
    InvalidContext,
    InvalidTransition,
    TRANSITIONS,
)


__all__ = [
    "ConversationNotFound",
    "EvidenceContent",
    "EvidenceItem",
    "IneligibleAssignee",
    "InquiryContext",
    "InquiryDetail",
    "InquiryNotFound",
    "InquiryService",
    "InquirySummary",
    "InvalidAgentDraft",
    "InvalidContext",
    "InvalidTransition",
    "TRANSITIONS",
    "UnreadableFile",
]


class InvalidAgentDraft(ValueError):
    """The agent's draft text exceeds the inquiry limits."""


class UnreadableFile(ValueError):
    """The requester cannot read the file: unknown, deleted or not theirs."""


# Limits shared by the context model and drafts built from agent output.
MAX_EVIDENCE = 50
MAX_EVIDENCE_ID = 200
MAX_DRAFT_FILES = 20


class EvidenceItem(BaseModel):
    """Evidence copied into the inquiry so it never depends on chat history."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["citation", "file"]
    title: str = Field(max_length=300)
    excerpt: str | None = Field(default=None, max_length=4000)
    location: str | None = Field(default=None, max_length=300)
    evidence_id: str | None = Field(default=None, max_length=MAX_EVIDENCE_ID)  # citation evidence ID
    file_id: UUID | None = None  # must also be attached to the inquiry


class InquiryContext(BaseModel):
    """Situation, observations, attempted actions, proposed fix and evidence."""

    model_config = ConfigDict(extra="forbid")
    situation: str = Field(default="", max_length=4000)
    observations: str = Field(default="", max_length=4000)
    attempted_actions: str = Field(default="", max_length=4000)
    proposed_fix: str = Field(default="", max_length=4000)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=MAX_EVIDENCE)


@dataclass(frozen=True)
class EvidenceContent:
    """An attached file's record and the location of its bytes on disk."""

    record: FileRecord
    path: Path


def _parse_context(raw: dict) -> InquiryContext:
    """Validate a stored context dict; raise `InvalidContext` when it is malformed."""

    try:
        return InquiryContext.model_validate(raw)
    except ValidationError as exc:
        raise InvalidContext("The stored inquiry context is not valid") from exc


def _check_evidence_files(context: InquiryContext, attached: set[UUID]) -> None:
    """Raise `InvalidContext` when evidence refers to a file not in `attached`."""

    missing = {item.file_id for item in context.evidence if item.file_id} - attached
    if missing:
        raise InvalidContext("Evidence refers to a file that is not attached to the inquiry")


class InquiryService:
    """Draft, submit and read inquiries on behalf of signed-in users."""

    def __init__(
        self, store: InquiryStore, conversations: ConversationStore, library: FileLibraryService
    ) -> None:
        self.store = store
        self.conversations = conversations
        self.library = library

    async def create_draft(
        self,
        user: Account,
        conversation_id: UUID,
        title: str,
        context: InquiryContext,
        file_ids: Sequence[UUID] = (),
    ) -> InquiryDetail:
        """Save a draft inquiry from a conversation the user owns.

        Args:
            user: The requester.
            conversation_id: An active conversation owned by `user`.
            title: Draft title; may be empty until submission.
            context: Validated context snapshot.
            file_ids: Library files to attach; each must be readable by `user` now.

        Returns:
            The saved draft with its attached files.

        Raises:
            ConversationNotFound: The conversation is missing, deleted or not the user's.
            UnreadableFile: A file is unknown, deleted or not the user's.
            InvalidContext: Evidence refers to a file that is not attached.
        """

        await self.conversations.require_active_owner(conversation_id, user.id)
        unique = list(dict.fromkeys(file_ids))
        for file_id in unique:
            await self._require_readable(user, file_id)
        _check_evidence_files(context, set(unique))
        inquiry_id = await self.store.create_draft(
            user.id, conversation_id, title, context.model_dump(mode="json"), unique
        )
        return await self.store.get_visible(inquiry_id, user.id)

    async def create_draft_from_agent(
        self, user: Account, conversation_id: UUID, draft: AgentInquiryDraft
    ) -> InquiryDetail:
        """Save the agent's draft as the user's inquiry draft.

        Copies the agent's citations as evidence, keeps only the photos the user
        can still read (each also becomes file evidence), and clips titles,
        locations and the evidence count to their limits. Text fields are not
        clipped.

        Args:
            user: The requester.
            conversation_id: The conversation the draft came from.
            draft: The agent's fields, citations and photo references.

        Returns:
            The saved draft; the title is empty when the agent wrote none.

        Raises:
            ConversationNotFound: The conversation is missing, deleted or not the user's.
            InvalidAgentDraft: A text field exceeds the inquiry limits.
            UnreadableFile: A kept photo stopped being readable before saving.
        """

        file_ids: list[UUID] = []
        file_evidence: list[EvidenceItem] = []
        for attachment in draft.attachments:
            if len(file_ids) >= MAX_DRAFT_FILES:
                break
            try:
                file_id = UUID(attachment.file_id)
                record = await self.library.get(user, file_id)
            except (ValueError, FileNotFound):
                continue
            if file_id in file_ids:
                continue
            file_ids.append(file_id)
            file_evidence.append(EvidenceItem(kind="file", title=record.filename[:300], file_id=file_id))
        # Photos keep their place; citations fill the remaining evidence slots.
        # A citation whose ID exceeds the limit is skipped, never shortened.
        citations = [
            EvidenceItem(
                kind="citation",
                title=item.title[:300],
                excerpt=item.excerpt[:4000] or None,
                location=item.location[:300] if item.location else None,
                evidence_id=item.evidence_id,
            )
            for item in draft.citations
            if len(item.evidence_id) <= MAX_EVIDENCE_ID
        ]
        evidence = citations[: MAX_EVIDENCE - len(file_evidence)] + file_evidence
        fields = draft.fields
        try:
            context = InquiryContext(
                situation=fields.situation,
                observations=fields.observations,
                attempted_actions=fields.attempted_actions,
                proposed_fix=fields.proposed_fix,
                evidence=evidence,
            )
        except ValidationError as exc:
            raise InvalidAgentDraft("The generated draft exceeds the inquiry limits") from exc
        return await self.create_draft(
            user, conversation_id, fields.title.strip()[:200], context, file_ids
        )

    async def list(self, user: Account, box: Box) -> list[InquirySummary]:
        """List inquiries sent by the user (drafts included) or assigned to them (no drafts)."""

        return await self.store.list_box(user.id, box)

    async def get(self, user: Account, inquiry_id: UUID) -> InquiryDetail:
        """Return an inquiry the user may open.

        Raises:
            InquiryNotFound: It is missing, deleted or not visible to the user.
        """

        return await self.store.get_visible(inquiry_id, user.id)

    async def update_draft(
        self,
        user: Account,
        inquiry_id: UUID,
        *,
        title: str | None = None,
        context: InquiryContext | None = None,
    ) -> InquiryDetail:
        """Change the title and/or context of the user's own draft.

        Raises:
            InquiryNotFound: It is missing, deleted or not the user's.
            InvalidTransition: It has already been submitted.
            InvalidContext: Evidence refers to a file that is not attached.
        """

        await self.store.update_draft(
            inquiry_id, user.id, title=title,
            context=None if context is None else context.model_dump(mode="json"),
            # Runs under the row lock, so a concurrent detach cannot slip between check and write.
            check_attached=(
                None if context is None
                else lambda attached: _check_evidence_files(context, attached)
            ),
        )
        return await self.store.get_visible(inquiry_id, user.id)

    async def delete_draft(self, user: Account, inquiry_id: UUID) -> None:
        """Soft-delete the user's own draft.

        Raises:
            InquiryNotFound: It is missing, deleted or not the user's.
            InvalidTransition: It has already been submitted.
        """

        await self.store.soft_delete_draft(inquiry_id, user.id)

    async def attach_file(self, user: Account, inquiry_id: UUID, file_id: UUID) -> None:
        """Attach a library file to the user's draft.

        Raises:
            InquiryNotFound: The draft is missing, deleted or not the user's.
            InvalidTransition: It has already been submitted.
            UnreadableFile: The file is unknown, deleted or not the user's.
        """

        detail = await self.store.get_visible(inquiry_id, user.id)
        if detail.summary.requested_by != user.id:
            raise InquiryNotFound(inquiry_id)
        await self._require_readable(user, file_id)
        await self.store.attach_file(inquiry_id, user.id, file_id)

    async def detach_file(self, user: Account, inquiry_id: UUID, file_id: UUID) -> None:
        """Remove a file from the user's draft.

        Raises:
            InquiryNotFound: The draft is missing, deleted or not the user's.
            InvalidTransition: It has already been submitted.
        """

        await self.store.detach_file(inquiry_id, user.id, file_id)

    async def submit(self, user: Account, inquiry_id: UUID, assigned_to: UUID) -> InquiryDetail:
        """Send the user's draft to an eligible expert; status becomes `open`.

        Raises:
            InquiryNotFound: The draft is missing, deleted or not the user's.
            InvalidTransition: It has already been submitted.
            InvalidContext: The title or situation is empty, or evidence refers
                to a file that is not attached.
            IneligibleAssignee: `assigned_to` is not an active, undeleted expert,
                or is the requester.
        """


        def check(title: str, raw_context: dict[str, Any], attached: set[UUID]) -> None:
            """Validate the row as stored under the lock; raise a rule error when incomplete."""

            context = _parse_context(raw_context)
            if not title.strip() or not context.situation.strip():
                raise InvalidContext("A title and a situation are required to submit")
            _check_evidence_files(context, attached)
            if assigned_to == user.id:
                # The requester would also be the assignee and could accept their own inquiry.
                raise IneligibleAssignee("An inquiry cannot be assigned to its requester")

        await self.store.submit(inquiry_id, user.id, assigned_to, check)
        return await self.store.get_visible(inquiry_id, user.id)

    async def record_event(
        self, user: Account, inquiry_id: UUID, kind: str, content: str | None
    ) -> InquiryDetail:
        """Record a review action or reply and return the updated inquiry.

        Args:
            user: The actor; their role in the inquiry is read from the stored row.
            inquiry_id: The inquiry to act on.
            kind: One of `accepted`, `edited`, `denied`, `question`, `reply`.
            content: Text of the event; required except for `accepted`.

        Raises:
            InquiryNotFound: It is missing, deleted or not visible to the user.
            InvalidTransition: The kind is not allowed for the current status or the user's role.
        """

        await self.store.record_event(inquiry_id, user.id, kind, content)
        return await self.store.get_visible(inquiry_id, user.id)

    async def experts(self) -> list[tuple[UUID, str]]:
        """Return `(id, display_name)` of accounts that can be assigned an inquiry."""

        return await self.store.eligible_experts()

    async def open_evidence(
        self, user: Account, inquiry_id: UUID, file_id: UUID
    ) -> EvidenceContent:
        """Locate an attached file for the requester or assignee.

        Works after the owner deleted the file from the library, because the
        inquiry keeps its own reference.

        Raises:
            InquiryNotFound: The inquiry is not visible, the file is not attached
                to it, or its bytes are missing from disk.
        """

        record = await self.store.get_attached_file(inquiry_id, file_id, user.id)
        path = self.library.blobs.path(record.storage_key)
        if not await asyncio.to_thread(path.is_file):
            raise InquiryNotFound(inquiry_id)
        return EvidenceContent(record=record, path=path)

    async def _require_readable(self, user: Account, file_id: UUID) -> None:
        """Raise `UnreadableFile` unless the user can read the library file."""

        try:
            await self.library.get(user, file_id)
        except FileNotFound as exc:
            raise UnreadableFile(str(file_id)) from exc

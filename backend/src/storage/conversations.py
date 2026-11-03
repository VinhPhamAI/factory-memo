"""Conversation list records in the application database.

Every query is scoped by owner and excludes soft-deleted rows, so a
conversation of another user and a missing one look the same. Messages are
stored by the standalone agent, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.storage.app_database import app_transaction
from backend.src.storage.orm import Conversation


# The list has no pagination yet; it returns the most recently active chats.
LIST_LIMIT = 200


class ConversationNotFound(LookupError):
    """No active conversation with this ID belongs to the user."""


@dataclass(frozen=True)
class ConversationRecord:
    """Public fields of a conversation."""

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


_COLUMNS = (Conversation.id, Conversation.title, Conversation.created_at, Conversation.updated_at)


def _owned(conversation_id: UUID, owner_id: UUID) -> tuple:
    return (
        Conversation.id == conversation_id,
        Conversation.owner_id == owner_id,
        Conversation.deleted_at.is_(None),
    )


class ConversationStore:
    """Reads and writes conversation rows through short transactions.

    Raises AppDatabaseUnavailable from every method when the database cannot
    be reached.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, owner_id: UUID, title: str) -> ConversationRecord:
        """Insert a conversation owned by `owner_id` and return it."""

        async with app_transaction(self._sessions) as session:
            conversation = Conversation(owner_id=owner_id, title=title)
            session.add(conversation)
            await session.flush()
            await session.refresh(conversation)
        return ConversationRecord(
            conversation.id, conversation.title, conversation.created_at, conversation.updated_at
        )

    async def list_owned(self, owner_id: UUID) -> list[ConversationRecord]:
        """Return the owner's active conversations, most recently active first."""

        async with app_transaction(self._sessions) as session:
            rows = await session.execute(
                select(*_COLUMNS)
                .where(Conversation.owner_id == owner_id, Conversation.deleted_at.is_(None))
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .limit(LIST_LIMIT)
            )
            return [ConversationRecord(*row) for row in rows]

    async def require_active_owner(self, conversation_id: UUID, owner_id: UUID) -> ConversationRecord:
        """Return the conversation if it is active and owned by `owner_id`.

        Raises:
            ConversationNotFound: It is missing, deleted or owned by another user.
        """

        async with app_transaction(self._sessions) as session:
            row = (await session.execute(select(*_COLUMNS).where(*_owned(conversation_id, owner_id)))).one_or_none()
        if row is None:
            raise ConversationNotFound(conversation_id)
        return ConversationRecord(*row)

    async def rename(self, conversation_id: UUID, owner_id: UUID, title: str) -> ConversationRecord:
        """Change the title of an owned active conversation.

        Raises:
            ConversationNotFound: It is missing, deleted or owned by another user.
        """

        return await self._update(conversation_id, owner_id, title=title)

    async def mark_active(self, conversation_id: UUID, owner_id: UUID) -> ConversationRecord:
        """Record a new message by moving `updated_at`, after checking ownership.

        Raises:
            ConversationNotFound: It is missing, deleted or owned by another user.
        """

        return await self._update(conversation_id, owner_id, updated_at=func.now())

    async def soft_delete(self, conversation_id: UUID, owner_id: UUID) -> None:
        """Hide an owned active conversation; the row stays for inquiries.

        Raises:
            ConversationNotFound: It is missing, already deleted or owned by another user.
        """

        await self._update(conversation_id, owner_id, deleted_at=func.now())

    async def _update(self, conversation_id: UUID, owner_id: UUID, **values: object) -> ConversationRecord:
        async with app_transaction(self._sessions) as session:
            row = (
                await session.execute(
                    update(Conversation)
                    .where(*_owned(conversation_id, owner_id))
                    .values(**values)
                    .returning(*_COLUMNS)
                    .execution_options(synchronize_session=False)
                )
            ).one_or_none()
        if row is None:
            raise ConversationNotFound(conversation_id)
        return ConversationRecord(*row)

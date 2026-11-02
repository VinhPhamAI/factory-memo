"""Factory machines referenced by conversations and inquiries."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.orm.base import Base, RecordDates, json_object, uuid_primary_key


class Machine(RecordDates, Base):
    """A factory machine with its identifier, model, location and other names."""

    __tablename__ = "machines"
    __table_args__ = (json_object("details"),)

    id: Mapped[UUID] = uuid_primary_key()
    # Unique even after soft deletion.
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

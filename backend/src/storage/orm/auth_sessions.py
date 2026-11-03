"""Login sessions and the digests of their bearer credentials."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.orm.base import Base, ChangeDates, sha256_hex, uuid_primary_key


class AuthSession(ChangeDates, Base):
    """One signed-in device: its current access and refresh credentials.

    Only SHA-256 digests of the random credentials are stored. Refresh replaces
    both digests; `expires_at` is fixed at login and is never extended.
    A revoked session stays revoked.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        sha256_hex("access_token_digest"),
        sha256_hex("refresh_token_digest"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    access_token_digest: Mapped[str] = mapped_column(Text, unique=True)
    refresh_token_digest: Mapped[str] = mapped_column(Text, unique=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

"""User accounts and their roles."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.orm.base import Base, RecordDates, allowed_values, uuid_primary_key


class UserRole(StrEnum):
    """Role that decides which actions an account may perform."""

    EMPLOYEE = "employee"
    EXPERT = "expert"
    ADMIN = "admin"


def normalize_username(username: str) -> str:
    """Convert a login name to its stored form.

    Args:
        username: Name typed at registration or login.

    Returns:
        The name without surrounding whitespace, in lower case.

    Raises:
        ValueError: The name contains no non-whitespace characters.
    """

    normalized = username.strip().lower()
    if not normalized:
        raise ValueError("A username is required.")
    return normalized


class User(RecordDates, Base):
    """One employee, expert or administrator account.

    Usernames stay unique after soft deletion, so they are never reused.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "username = lower(btrim(username)) AND username <> ''",
            name="username_normalized",
        ),
        allowed_values("role", UserRole),
    )

    id: Mapped[UUID] = uuid_primary_key()
    username: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )

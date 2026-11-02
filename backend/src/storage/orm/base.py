"""Declarative base and shared column rules for the backend application tables.

These SQLAlchemy models describe the tables defined in
docs/architecture/database-schema.md. Alembic revisions in backend/migrations
create them; the models never create or alter tables at application startup.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, FetchedValue, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_utils.compat import uuid7


# Deterministic constraint and index names, so migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Shared by every table: one text search configuration for all collections.
# `simple` lowercases words without language-specific stemming, which suits
# mixed Vietnamese/English factory text.
TEXT_SEARCH_CONFIG = "simple"


def new_id() -> UUID:
    """Create a UUIDv7 application ID.

    Returns:
        A standard-library UUID whose leading bits contain the creation time.
        The time component does not replace `created_at` or order actions
        across services.
    """

    return uuid7()


class Base(DeclarativeBase):
    """Declarative base for backend tables managed by Alembic."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Read trigger-maintained values such as updated_at through RETURNING,
    # instead of expiring them and loading them later (not allowed in async code).
    __mapper_args__ = {"eager_defaults": True}


class ChangeDates:
    """Creation and last-update times of a record that is never hidden.

    A database trigger keeps `created_at` unchanged and sets `updated_at` on
    each update that does not set it explicitly.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        server_onupdate=FetchedValue(),
    )


class RecordDates(ChangeDates):
    """Creation, last-update and soft-deletion times of an editable record.

    Setting `deleted_at` hides the record without removing it.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def uuid_primary_key() -> Mapped[UUID]:
    """Declare a `uuid` primary key generated in application code as UUIDv7."""

    return mapped_column(Uuid, primary_key=True, default=new_id)


def allowed_values(column: str, values: type[StrEnum]) -> CheckConstraint:
    """Reject column values outside an enumeration.

    Args:
        column: Column name checked by the constraint.
        values: Enumeration whose string values are allowed.

    Returns:
        A check constraint named `<column>_allowed`.
    """

    listed = ", ".join(f"'{value.value}'" for value in values)
    return CheckConstraint(f"{column} IN ({listed})", name=f"{column}_allowed")


def json_object(column: str) -> CheckConstraint:
    """Require a `jsonb` column to hold a JSON object (named fields)."""

    return CheckConstraint(
        f"jsonb_typeof({column}) = 'object'", name=f"{column}_is_object"
    )


def json_array(column: str) -> CheckConstraint:
    """Require a `jsonb` column to hold a JSON array (a list)."""

    return CheckConstraint(
        f"jsonb_typeof({column}) = 'array'", name=f"{column}_is_array"
    )


def sha256_hex(column: str) -> CheckConstraint:
    """Require a text column to hold a lowercase hexadecimal SHA-256 value."""

    return CheckConstraint(f"{column} ~ '^[0-9a-f]{{64}}$'", name=f"{column}_hex")

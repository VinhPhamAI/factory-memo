"""Uploaded-file records; file bytes stay in the file store."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.orm.base import Base, RecordDates, sha256_hex, uuid_primary_key


class File(RecordDates, Base):
    """Metadata of an uploaded file whose bytes live outside PostgreSQL.

    A file ID alone grants no download access; permissions follow the source
    or inquiry that references the file.
    """

    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_bytes_not_negative"),
        sha256_hex("sha256"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Location in the file store; not a public download URL.
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    # Lowercase hex digest; identical files may share it.
    sha256: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)

"""SQLAlchemy models of the backend application tables managed by Alembic.

See docs/architecture/database-schema.md for the table design and
docs/development/database-migrations.md for schema changes.
"""

from backend.src.storage.orm.base import (
    NAMING_CONVENTION,
    TEXT_SEARCH_CONFIG,
    Base,
    ChangeDates,
    RecordDates,
    new_id,
)
from backend.src.storage.orm.auth_sessions import AuthSession
from backend.src.storage.orm.files import File
from backend.src.storage.orm.inquiries import (
    EVENT_KINDS_REQUIRING_CONTENT,
    Conversation,
    Inquiry,
    InquiryEvent,
    InquiryEventKind,
    InquiryFile,
    InquiryStatus,
)
from backend.src.storage.orm.knowledge import (
    ChunkStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeChunkSource,
    KnowledgeSource,
    SourceKind,
    SourceStatus,
)
from backend.src.storage.orm.machines import Machine
from backend.src.storage.orm.processing import (
    ConflictStatus,
    ContentSignature,
    ExtractionMode,
    ExtractionRun,
    IngestionJob,
    JobStage,
    JobStatus,
    KnowledgeConflict,
    RunStatus,
)
from backend.src.storage.orm.users import User, UserRole, normalize_username

__all__ = [
    "EVENT_KINDS_REQUIRING_CONTENT",
    "NAMING_CONVENTION",
    "TEXT_SEARCH_CONFIG",
    "AuthSession",
    "Base",
    "ChangeDates",
    "ChunkStatus",
    "ConflictStatus",
    "ContentSignature",
    "Conversation",
    "ExtractionMode",
    "ExtractionRun",
    "File",
    "IngestionJob",
    "Inquiry",
    "InquiryEvent",
    "InquiryEventKind",
    "InquiryFile",
    "InquiryStatus",
    "JobStage",
    "JobStatus",
    "KnowledgeBase",
    "KnowledgeChunk",
    "KnowledgeChunkSource",
    "KnowledgeConflict",
    "KnowledgeSource",
    "Machine",
    "RecordDates",
    "RunStatus",
    "SourceKind",
    "SourceStatus",
    "User",
    "UserRole",
    "new_id",
    "normalize_username",
]

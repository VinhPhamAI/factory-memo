"""Repositories of the knowledge tables in the application database (`APP_DATABASE_URL`).

Each repository takes an `async_sessionmaker`, owns its transactions and
raises `KnowledgeStorageUnavailable` when the database cannot be reached.
"""

from backend.src.storage.repositories.base import KnowledgeStorageUnavailable
from backend.src.storage.repositories.chunks import ChunkRepository
from backend.src.storage.repositories.content_signatures import ContentSignatureRepository
from backend.src.storage.repositories.extraction_runs import (
    ExtractionRun,
    ExtractionRunRepository,
    RunConflict,
)
from backend.src.storage.repositories.ingestion_jobs import IngestionJobRepository, Job
from backend.src.storage.repositories.knowledge_bases import (
    KnowledgeBaseEntry,
    KnowledgeBaseLimitReached,
    KnowledgeBaseNameTaken,
    KnowledgeBaseRepository,
)
from backend.src.storage.repositories.knowledge_conflicts import ConflictRepository
from backend.src.storage.repositories.sources import (
    FileEntry,
    NewFile,
    SourceEntry,
    SourceRepository,
)

__all__ = [
    "ChunkRepository",
    "ConflictRepository",
    "ContentSignatureRepository",
    "ExtractionRun",
    "ExtractionRunRepository",
    "FileEntry",
    "IngestionJobRepository",
    "Job",
    "KnowledgeBaseEntry",
    "KnowledgeBaseLimitReached",
    "KnowledgeBaseNameTaken",
    "KnowledgeBaseRepository",
    "KnowledgeStorageUnavailable",
    "NewFile",
    "RunConflict",
    "SourceEntry",
    "SourceRepository",
]

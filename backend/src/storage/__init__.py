"""Knowledge base persistence: PostgreSQL + pgvector via langchain-postgres."""

from backend.src.storage.knowledge_store import (
    KnowledgeStore,
    StorageError,
    build_knowledge_filter,
)
from backend.src.storage.models import (
    ConversationStatus,
    KnowledgeRecord,
    KnowledgeSearchHit,
    RawConversation,
)

__all__ = [
    "ConversationStatus",
    "KnowledgeRecord",
    "KnowledgeSearchHit",
    "KnowledgeStore",
    "RawConversation",
    "StorageError",
    "build_knowledge_filter",
]

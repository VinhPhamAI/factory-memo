"""Turn a raw conversation into stored, embedded knowledge records."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from backend.src.services.knowledge_extraction import KnowledgeExtractionResult
from backend.src.storage.models import (
    ConversationStatus,
    KnowledgeRecord,
    RawConversation,
)


class SupportsExtract(Protocol):
    def extract(self, raw_conversation: str) -> KnowledgeExtractionResult: ...


class SupportsKnowledgeWrite(Protocol):
    async def add_conversation(
        self,
        transcript: str,
        *,
        stated_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RawConversation: ...

    async def mark_conversation(
        self,
        conversation_id: UUID,
        status: ConversationStatus,
        *,
        error: str | None = None,
    ) -> None: ...

    async def add_knowledge(self, records: list[KnowledgeRecord]) -> list[UUID]: ...


@dataclass(frozen=True)
class IngestionResult:
    evidence_id: UUID
    records: tuple[KnowledgeRecord, ...]

    @property
    def knowledge_ids(self) -> tuple[UUID, ...]:
        return tuple(record.id for record in self.records)


class KnowledgeIngestionService:
    """Store the transcript as evidence, extract knowledge, then embed and write it."""

    def __init__(self, extractor: SupportsExtract, store: SupportsKnowledgeWrite):
        self._extractor = extractor
        self._store = store

    async def ingest(
        self,
        transcript: str,
        *,
        stated_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        if not transcript or not transcript.strip():
            raise ValueError("transcript must not be empty")

        # Evidence is persisted first so a failed extraction stays traceable.
        conversation = await self._store.add_conversation(
            transcript.strip(), stated_by=stated_by, metadata=metadata
        )
        try:
            # The extractor uses a blocking LLM client.
            extracted = await asyncio.to_thread(self._extractor.extract, transcript)
            print(extracted)
            records = [
                KnowledgeRecord.from_extracted(
                    item,
                    evidence_id=conversation.id,
                    stated_by=stated_by,
                )
                for item in extracted.knowledge_items
            ]
            print(records)
            await self._store.add_knowledge(records)
        except Exception as exc:
            await self._store.mark_conversation(
                conversation.id,
                ConversationStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
            raise
        await self._store.mark_conversation(conversation.id, ConversationStatus.PROCESSED)
        return IngestionResult(evidence_id=conversation.id, records=tuple(records))

"""Turn raw conversations into stored, embedded knowledge records."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from backend.src.services.knowledge_extraction import KnowledgeExtractionResult
from backend.src.storage.models import (
    ConversationStatus,
    KnowledgeRecord,
    KnowledgeStatus,
    RawConversation,
)


logger = logging.getLogger(__name__)


class SupportsExtract(Protocol):
    async def aextract(self, raw_conversation: str) -> KnowledgeExtractionResult: ...


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

    async def embed_knowledge(self, records: Sequence[KnowledgeRecord]) -> None: ...

    async def add_knowledge(self, records: list[KnowledgeRecord]) -> list[UUID]: ...


@dataclass(frozen=True)
class IngestionInput:
    transcript: str
    stated_by: str | None = None
    metadata: dict[str, Any] | None = field(default=None)
    # Merged into the context of every record extracted from this transcript,
    # overriding what the LLM wrote for the same keys (e.g. the source system).
    context: dict[str, Any] | None = field(default=None)


@dataclass(frozen=True)
class IngestionResult:
    evidence_id: UUID
    records: tuple[KnowledgeRecord, ...]

    @property
    def knowledge_ids(self) -> tuple[UUID, ...]:
        return tuple(record.id for record in self.records)


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]


class KnowledgeIngestionService:
    """Store transcripts as evidence, extract knowledge, then embed and write it."""

    def __init__(
        self, extractor: SupportsExtract, store: SupportsKnowledgeWrite,
        *, extraction_metadata: dict[str, Any] | None = None,
        knowledge_status: KnowledgeStatus = KnowledgeStatus.APPROVED,
    ):
        self._extractor = extractor
        self._store = store
        self._extraction_metadata = dict(extraction_metadata or {})
        # DRAFT when the knowledge base requires expert review.
        self._knowledge_status = KnowledgeStatus(knowledge_status)

    async def ingest(
        self,
        transcript: str,
        *,
        stated_by: str | None = None,
        metadata: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> IngestionResult:
        (result,) = await self.ingest_batch(
            [IngestionInput(transcript, stated_by, metadata, context)], concurrency=1
        )
        if isinstance(result, BaseException):
            raise result
        return result

    async def ingest_batch(
        self,
        inputs: Sequence[IngestionInput],
        *,
        concurrency: int = 8,
    ) -> list[IngestionResult | Exception]:
        """Ingest many transcripts with up to `concurrency` LLM calls in flight.

        Returns one entry per input, in order: its result, or the exception
        that failed it. One failed transcript does not affect the others.
        """

        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        results: list[IngestionResult | Exception | None] = [None] * len(inputs)

        # Evidence is persisted first so a failed extraction stays traceable.
        conversations: dict[int, RawConversation] = {}
        for index, item in enumerate(inputs):
            if not item.transcript or not item.transcript.strip():
                results[index] = ValueError("transcript must not be empty")
                continue
            metadata = dict(item.metadata or {})
            if self._extraction_metadata:
                # Dataset metadata cannot spoof the active extraction settings.
                metadata["extraction"] = dict(self._extraction_metadata)
            conversations[index] = await self._store.add_conversation(
                item.transcript.strip(), stated_by=item.stated_by, metadata=metadata
            )

        semaphore = asyncio.Semaphore(concurrency)

        async def extract(index: int) -> KnowledgeExtractionResult:
            async with semaphore:
                return await self._extractor.aextract(conversations[index].transcript)

        indexes = list(conversations)
        extracted = await asyncio.gather(
            *(extract(index) for index in indexes), return_exceptions=True
        )

        records: dict[int, list[KnowledgeRecord]] = {}
        for index, outcome in zip(indexes, extracted, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):  # e.g. CancelledError
                    raise outcome
                await self._fail(conversations[index], outcome)
                results[index] = outcome
                continue
            group = [
                KnowledgeRecord.from_extracted(
                    item,
                    evidence_id=conversations[index].id,
                    stated_by=inputs[index].stated_by,
                    status=self._knowledge_status,
                )
                for item in outcome.knowledge_items
            ]
            # Support links point to positions in the result; store record ids.
            for record, item in zip(group, outcome.knowledge_items, strict=True):
                record.supported_by = [group[position].id for position in item.supported_by]
            records[index] = group
            if inputs[index].context:
                for record in records[index]:
                    record.context = {**record.context, **inputs[index].context}

        # One embedding call for the whole batch; if it fails, add_knowledge
        # embeds each conversation's records on its own, isolating the failure.
        batch_records = [record for group in records.values() for record in group]
        if batch_records:
            try:
                await self._store.embed_knowledge(batch_records)
            except Exception:
                logger.warning(
                    "Batch embedding failed; embedding per conversation", exc_info=True
                )

        for index, group in records.items():
            conversation = conversations[index]
            try:
                await self._store.add_knowledge(group)
            except Exception as exc:
                await self._fail(conversation, exc)
                results[index] = exc
                continue
            await self._store.mark_conversation(conversation.id, ConversationStatus.PROCESSED)
            results[index] = IngestionResult(evidence_id=conversation.id, records=tuple(group))

        return results  # type: ignore[return-value]  # every slot is filled

    async def _fail(self, conversation: RawConversation, exc: Exception) -> None:
        await self._store.mark_conversation(
            conversation.id, ConversationStatus.FAILED, error=_error_text(exc)
        )

"""Near-duplicate signatures of extracted source text in the application database.

Stores the MinHash LSH bands computed by `storage/content_signatures.py`, one
row per source. `ExtractionRunRepository.overlap_candidates` finds sources
sharing a band. Scores only warn reviewers; nothing is merged or blocked.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.storage.orm import ContentSignature, ExtractionRun, RunStatus
from backend.src.storage.repositories.base import Repository


class ContentSignatureRepository(Repository):
    """Signatures of the sources of one knowledge base."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], knowledge_base_id: UUID) -> None:
        super().__init__(sessions)
        self.knowledge_base_id = knowledge_base_id

    async def put(
        self,
        source_id: UUID,
        *,
        content: str,
        version: str,
        shingle_count: int,
        bands: list[str],
    ) -> None:
        """Save or replace the signature of a source.

        Args:
            source_id: Source the text belongs to.
            content: Normalized content hash of the text.
            version: Signature algorithm and parameters.
            shingle_count: Number of shingles of the text.
            bands: LSH band keys.

        Raises:
            sqlalchemy.exc.IntegrityError: The source is not in the knowledge base.
        """

        values = {
            "content_sha256": content,
            "version": version,
            "shingle_count": shingle_count,
            "bands": list(bands),
        }
        statement = insert(ContentSignature).values(
            source_id=source_id, knowledge_base_id=self.knowledge_base_id, **values
        )
        async with self._transaction() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[ContentSignature.source_id], set_=values
                )
            )

    async def unsigned(self, version: str, limit: int = 100) -> list[UUID]:
        """Return sources with a running or done run but no signature of `version`."""

        signed = (
            select(ContentSignature.source_id)
            .where(
                ContentSignature.source_id == ExtractionRun.source_id,
                ContentSignature.version == version,
            )
            .exists()
        )
        query = (
            select(ExtractionRun.source_id)
            .where(
                ExtractionRun.knowledge_base_id == self.knowledge_base_id,
                ExtractionRun.status.in_((RunStatus.RUNNING.value, RunStatus.DONE.value)),
                ~signed,
            )
            .group_by(ExtractionRun.source_id)
            .order_by(func.min(ExtractionRun.created_at))
            .limit(limit)
        )
        async with self._transaction() as session:
            return list((await session.scalars(query)).all())

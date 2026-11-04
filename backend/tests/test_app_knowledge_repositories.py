"""Integration tests of the knowledge repositories on the application database.

Need PostgreSQL + pgvector; see `test_database_migrations.py` for the command:
    TEST_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent \
        uv run python -m unittest backend.tests.test_app_knowledge_repositories -v

Each test class migrates its own disposable database and drops it afterwards.
"""

from __future__ import annotations

import asyncio
import hashlib
import unittest
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.src.agents.rag.chunking import Chunk
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage.app_database import create_app_engine, create_session_factory
from backend.src.storage.job_queue import STALE_ERROR, JobConflict, JobLeaseLost
from backend.src.storage.extraction_runs import ABANDONED
from backend.src.storage.knowledge_conflicts import ConflictRow, ordered
from backend.src.storage.models import KnowledgeLinkUpdate, KnowledgeRecord, KnowledgeStatus
from backend.src.storage.orm import (
    File,
    IngestionJob,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeChunkSource,
    KnowledgeSource,
    SourceKind,
    User,
    UserRole,
)
from backend.src.storage.repositories import (
    ChunkRepository,
    ConflictRepository,
    ContentSignatureRepository,
    ExtractionRunRepository,
    IngestionJobRepository,
    KnowledgeBaseLimitReached,
    KnowledgeBaseNameTaken,
    KnowledgeBaseRepository,
    NewFile,
    RunConflict,
    SourceRepository,
)
from backend.src.storage.repositories.lifecycle import InactiveKnowledgeBase, InactiveSource
from backend.tests.test_database_migrations import DATABASE_URL, MigratedDatabaseTest

# Content hashes are checked to be SHA-256 hex digests.
H_C1, H_C2, H_C3, H_C9, H_NEW = (hashlib.sha256(name.encode()).hexdigest()
                                 for name in ("c1", "c2", "c3", "c9", "new"))


class RepositoryTest(MigratedDatabaseTest, unittest.IsolatedAsyncioTestCase):
    """A migrated database, an async session factory and data helpers."""

    async def asyncSetUp(self) -> None:
        self.app_engine = create_app_engine(self.database.url)
        self.sessions = create_session_factory(self.app_engine)
        self.user = self.add_user()
        self.kb = self.add_kb()

    async def asyncTearDown(self) -> None:
        await self.app_engine.dispose()

    def add_user(self, role: UserRole = UserRole.EXPERT) -> User:
        name = f"user-{uuid4().hex[:8]}"
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            user = User(username=name, display_name=name, password_hash="not-a-hash", role=role.value)
            session.add(user)
        return user

    def add_kb(self) -> KnowledgeBase:
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            kb = KnowledgeBase(
                name=f"kb_{uuid4().hex[:8]}", title="KB", template="general", created_by=self.user.id
            )
            session.add(kb)
        return kb

    async def add_source(self, kb: KnowledgeBase | None = None, text_value: str = "note") -> UUID:
        source = await SourceRepository(self.sessions).create(
            (kb or self.kb).id, kind=SourceKind.TEXT, title="Note",
            submitted_by=self.user.id, extracted_text=text_value,
        )
        return source.id

    def count(self, model: type) -> int:
        with self.engine.connect() as conn:
            return conn.scalar(select(func.count()).select_from(model))


def record(**values) -> KnowledgeRecord:
    return KnowledgeRecord(subject="Pump P-101", claim="Check the seal first.",
                           type=KnowledgeType.RULE, **values)


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class KnowledgeBaseRepositoryTest(RepositoryTest):
    async def test_names_stay_taken_after_soft_deletion(self) -> None:
        repository = KnowledgeBaseRepository(self.sessions)
        created = await repository.create(
            name="line_a", title="Line A", description="", template="general", created_by=self.user.id
        )
        self.assertEqual((created.template, created.created_by, created.description),
                         ("general", self.user.id, ""))
        with self.assertRaises(KnowledgeBaseNameTaken):
            await repository.create(name="line_a", title="Again", description="", template="general",
                                    created_by=self.user.id)
        with self.engine.begin() as conn:
            conn.execute(update(KnowledgeBase).where(KnowledgeBase.id == created.id)
                         .values(deleted_at=datetime.now(timezone.utc)))
        self.assertIsNone(await repository.get_by_name("line_a"))
        self.assertNotIn("line_a", [entry.name for entry in await repository.list_active()])
        with self.assertRaises(KnowledgeBaseNameTaken):
            await repository.create(name="line_a", title="Again", description="", template="general",
                                    created_by=self.user.id)

    async def test_detail_counts_source_status_and_chunk_kind(self) -> None:
        """Include zero-filled counts, grouped by status and chunk kind."""

        sources = SourceRepository(self.sessions)
        chunks = ChunkRepository(self.sessions)
        for status in ("received", "processing", "processed", "failed"):
            source = await self.add_source()
            await sources.update(self.kb.id, source, status=status)
        await chunks.add_records(self.kb.id, source, [record(status=KnowledgeStatus.APPROVED)])
        await chunks.add_passages(
            self.kb.id, source, [Chunk(doc_id="manual", index=0, text="Pump inspection")],
            [[1.0, 0.0, 0.0]],
        )
        repository = KnowledgeBaseRepository(self.sessions)
        detail = await repository.detail(self.kb.name)
        self.assertEqual(detail.created_by, self.user.username)
        self.assertEqual(detail.source_counts, dict.fromkeys(
            ("received", "processing", "processed", "failed"), 1,
        ))
        self.assertEqual(detail.chunk_counts, {
            "knowledge": {"draft": 0, "approved": 1, "deprecated": 0},
            "document_passage": {"draft": 1, "approved": 0, "deprecated": 0},
        })
        await repository.delete(self.kb.name)
        self.assertIsNone(await repository.detail(self.kb.name))

    async def test_the_limit_counts_active_knowledge_bases(self) -> None:
        repository = KnowledgeBaseRepository(self.sessions)
        active = len(await repository.list_active())
        with self.assertRaises(KnowledgeBaseLimitReached):
            await repository.create(name="over", title="Over", description="", template="general",
                                    created_by=self.user.id, limit=active)
        created = await repository.create(name="under", title="Under", description="d",
                                          template="general", created_by=self.user.id, limit=active + 1)
        self.assertEqual((await repository.get_by_name("under")).id, created.id)


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class SourceRepositoryTest(RepositoryTest):
    def new_file(self, key: str | None = None) -> NewFile:
        return NewFile(filename="manual.txt", content_type="text/plain", size_bytes=4,
                       storage_key=key or f"{uuid4().hex}/content", sha256="a" * 64)

    async def test_an_upload_writes_its_file_and_source_together_or_neither(self) -> None:
        repository = SourceRepository(self.sessions)
        source = await repository.create(self.kb.id, kind=SourceKind.DOCUMENT, title="manual.txt",
                                         submitted_by=self.user.id, file=self.new_file("taken/content"))
        self.assertEqual((source.status, source.file.filename), ("received", "manual.txt"))
        with Session(self.engine) as session:
            self.assertEqual(session.get(File, source.file.id).uploaded_by, self.user.id)
        files, sources = self.count(File), self.count(KnowledgeSource)
        with self.assertRaises(InactiveKnowledgeBase):
            # No file is inserted for an inactive KB.
            await repository.create(uuid4(), kind=SourceKind.DOCUMENT, title="x",
                                    submitted_by=self.user.id, file=self.new_file())
        with self.assertRaises(IntegrityError):
            await repository.create(self.kb.id, kind=SourceKind.DOCUMENT, title="y",
                                    submitted_by=self.user.id, file=self.new_file("taken/content"))
        self.assertEqual((self.count(File), self.count(KnowledgeSource)), (files, sources))
        with self.assertRaises(ValueError):
            await repository.create(self.kb.id, kind=SourceKind.IMAGE, title="z", submitted_by=self.user.id)

    async def test_removal_keeps_a_file_shared_by_another_source(self) -> None:
        """Only the removal of the last active reference soft-deletes the file."""

        repository = SourceRepository(self.sessions)
        upload = await repository.create(
            self.kb.id, kind=SourceKind.DOCUMENT, title="manual.txt",
            submitted_by=self.user.id, file=self.new_file(),
        )
        other_kb = self.add_kb()
        other_source = await self.add_source(other_kb)
        with self.engine.begin() as conn:
            conn.execute(update(KnowledgeSource).where(
                KnowledgeSource.id == other_source,
            ).values(file_id=upload.file.id))
        await repository.remove(self.kb.id, upload.id, actor_id=self.user.id, role=self.user.role)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(File, upload.file.id).deleted_at)
        await repository.remove(other_kb.id, other_source, actor_id=self.user.id, role=self.user.role)
        with Session(self.engine) as session:
            self.assertIsNotNone(session.get(File, upload.file.id).deleted_at)

    async def test_pages_count_chunks_and_updates_merge_metadata(self) -> None:
        repository = SourceRepository(self.sessions)
        first = await self.add_source()
        second = await self.add_source()
        await ChunkRepository(self.sessions).add_records(
            self.kb.id, second, [record(), record(status=KnowledgeStatus.APPROVED)]
        )
        page, total = await repository.list_page(self.kb.id, limit=1, offset=0)
        self.assertEqual((total, [entry.id for entry in page]), (2, [second]))
        self.assertEqual(page[0].chunk_counts, {"draft": 1, "approved": 1})
        await repository.update(self.kb.id, first, metadata={"speakers": ["A"]})
        updated = await repository.update(self.kb.id, first, status="processed",
                                          metadata={"conversion": {"encoding": "utf-8"}})
        self.assertEqual(updated.status, "processed")
        self.assertEqual(updated.metadata, {"speakers": ["A"], "conversion": {"encoding": "utf-8"}})
        other = self.add_kb()
        self.assertIsNone(await repository.get(other.id, first))
        self.assertIsNone(await repository.update(other.id, first, status="failed"))


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class ChunkRepositoryTest(RepositoryTest):
    async def test_records_round_trip_and_cannot_link_another_kb_source(self) -> None:
        repository = ChunkRepository(self.sessions)
        source = await self.add_source()
        stored = record(context={"equipment": "P-101"}, stated_by="speaker_1", embedding=[1.0, 0.0, 0.0])
        await repository.add_records(self.kb.id, source, [stored], leading_keys=("equipment",))
        (loaded,) = await repository.get_records(self.kb.id, [uuid4(), stored.id])
        self.assertEqual((loaded.id, loaded.context, loaded.evidence_id, loaded.stated_by),
                         (stored.id, {"equipment": "P-101"}, source, "speaker_1"))
        self.assertEqual([item.id for item in await repository.records_for_source(self.kb.id, source)],
                         [stored.id])
        chunks = self.count(KnowledgeChunk)
        other = await self.add_source(self.add_kb())
        with self.assertRaises(IntegrityError):
            await repository.add_records(self.kb.id, other, [record()])
        self.assertEqual(self.count(KnowledgeChunk), chunks)

    async def test_review_appends_history_and_draft_clears_the_reviewer(self) -> None:
        repository = ChunkRepository(self.sessions)
        source = await self.add_source()
        stored = record(revision={"history": [{"action": "extracted"}]})
        await repository.add_records(self.kb.id, source, [stored])
        approved = await repository.review_record(self.kb.id, stored.id, KnowledgeStatus.APPROVED,
                                                  reviewer_id=self.user.id, event={"action": "approve"})
        self.assertEqual(approved.status, KnowledgeStatus.APPROVED)
        draft = await repository.review_record(self.kb.id, stored.id, KnowledgeStatus.DRAFT,
                                               reviewer_id=self.user.id, event={"action": "reset"})
        self.assertEqual([entry["action"] for entry in draft.revision["history"]],
                         ["extracted", "approve", "reset"])
        with self.engine.connect() as conn:
            row = conn.execute(select(KnowledgeChunk.reviewed_by, KnowledgeChunk.reviewed_at)
                               .where(KnowledgeChunk.id == stored.id)).one()
        self.assertEqual(tuple(row), (None, None))
        self.assertIsNone(await repository.review_record(self.add_kb().id, stored.id,
                                                         KnowledgeStatus.APPROVED,
                                                         reviewer_id=self.user.id, event={}))

    async def test_records_links_and_consolidation_updates_commit_together(self) -> None:
        repository = ChunkRepository(self.sessions)
        old_source, new_source = await self.add_source(), await self.add_source()
        old = record()
        await repository.add_records(self.kb.id, old_source, [old])
        new = record(evidence_id=new_source)
        await repository.add_records_and_updates(
            self.kb.id,
            new_source,
            [new],
            updates=[KnowledgeLinkUpdate(
                old.id,
                supported_by=(new.id,),
                event={"action": "supported_by_case"},
            )],
        )
        loaded_old, loaded_new = await repository.get_records(
            self.kb.id, [old.id, new.id]
        )
        self.assertEqual(loaded_old.supported_by, [new.id])
        self.assertEqual(loaded_old.revision["history"][-1]["action"], "supported_by_case")
        self.assertEqual(loaded_new.evidence_id, new_source)

        before = self.count(KnowledgeChunk)
        with self.assertRaises(LookupError):
            await repository.add_records_and_updates(
                self.kb.id,
                new_source,
                [record()],
                updates=[KnowledgeLinkUpdate(uuid4(), event={"action": "missing"})],
            )
        self.assertEqual(self.count(KnowledgeChunk), before)

    async def test_document_passages_keep_doc_index_and_source_reference(self) -> None:
        repository = ChunkRepository(self.sessions)
        source = await self.add_source()
        chunks = [
            Chunk(doc_id=str(source), index=0, text="First passage", title="Manual"),
            Chunk(doc_id=str(source), index=1, text="Second passage", title="Manual"),
        ]
        ids = await repository.add_passages(
            self.kb.id, source, chunks, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        )
        passages = await repository.passages_for_source(self.kb.id, source)
        self.assertEqual(
            [(item["id"], item["doc_id"], item["index"]) for item in passages],
            [(str(ids[0]), str(source), 0), (str(ids[1]), str(source), 1)],
        )
        with self.engine.connect() as connection:
            references = connection.execute(
                select(KnowledgeChunkSource.references)
                .where(KnowledgeChunkSource.source_id == source)
                .order_by(KnowledgeChunkSource.chunk_id)
            ).scalars().all()
        self.assertEqual(
            sorted(references, key=lambda item: item[0]["chunk_index"]),
            [
                [{"type": "passage", "chunk_index": 0}],
                [{"type": "passage", "chunk_index": 1}],
            ],
        )


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class IngestionJobRepositoryTest(RepositoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.queue = IngestionJobRepository(self.sessions, max_attempts=3, retry_delay_s=0,
                                            channel_prefix=f"test_{uuid4().hex[:8]}")

    async def asyncTearDown(self) -> None:
        # Claims are global per stage; keep other tests' jobs out of the next claim.
        with self.engine.begin() as conn:
            conn.execute(update(IngestionJob).where(IngestionJob.status.in_(("queued", "running")))
                         .values(status="failed", locked_by=None))
        await super().asyncTearDown()

    def source_status(self, source_id: UUID) -> str:
        with self.engine.connect() as conn:
            return conn.scalar(select(KnowledgeSource.status).where(KnowledgeSource.id == source_id))

    async def test_kb_deletion_fails_queued_and_discards_prepared_chunks(self) -> None:
        """Let a claimed job finish after deletion and refuse its pending writes."""

        active = await self.add_source()
        queued = await self.add_source()
        await self.queue.enqueue(self.kb.id, active, "extract", mode="knowledge", next_stages=["conflict"])
        running = await self.queue.claim("extract", "worker")
        await self.queue.enqueue(self.kb.id, queued, "extract", mode="knowledge")
        self.assertTrue(await KnowledgeBaseRepository(self.sessions).delete(self.kb.name))

        latest = await self.queue.latest(self.kb.id)
        self.assertEqual(latest[running.source_id].status, "running")
        self.assertEqual((latest[queued].status, latest[queued].error), ("failed", "KB_DELETED"))
        self.assertIsNone(await self.queue.claim("extract", "another"))

        async def persist(session):
            await ChunkRepository(self.sessions).add_records_and_updates(
                self.kb.id, active, [record()], session=session,
            )

        self.assertIsNone(await self.queue.complete(running, "worker", persist=persist))
        latest = await self.queue.latest(self.kb.id)
        self.assertEqual((latest[active].status, latest[active].error), ("failed", "KB_DELETED"))
        self.assertEqual(await ChunkRepository(self.sessions).records_for_source(self.kb.id, active), [])

    async def test_running_source_cannot_be_removed(self) -> None:
        """A running job wins the removal race and remains owned by its worker."""

        from backend.src.storage.repositories.sources import SourceJobRunning

        source = await self.add_source()
        await self.queue.enqueue(self.kb.id, source, "convert")
        running = await self.queue.claim("convert", "worker")
        sources = SourceRepository(self.sessions)
        with self.assertRaises(SourceJobRunning):
            await sources.remove(self.kb.id, source, actor_id=self.user.id, role=self.user.role)
        await self.queue.complete(running, "worker")
        self.assertTrue(await sources.remove(self.kb.id, source, actor_id=self.user.id, role=self.user.role))

    async def test_enqueue_claim_and_chain_the_next_stage(self) -> None:
        source = await self.add_source()
        job = await self.queue.enqueue(self.kb.id, source, "convert", mode="documents",
                                       next_stages=["extract"], options={"confirm_overlap": True})
        self.assertEqual((job.status, job.attempts, job.options), ("queued", 0, {"confirm_overlap": True}))
        self.assertIsNone(await self.queue.claim("extract", "w1"))
        claimed = await self.queue.claim("convert", "w1")
        self.assertEqual((claimed.id, claimed.status, claimed.attempts, claimed.locked_by),
                         (job.id, "running", 1, "w1"))
        self.assertEqual(self.source_status(source), "processing")
        self.assertIsNone(await self.queue.claim("convert", "w2"))
        following = await self.queue.complete(claimed, "w1", {"converter": "text"})
        self.assertEqual((following.stage, following.mode, following.parent_id, following.next_stages),
                         ("extract", "documents", job.id, ()))
        self.assertEqual(following.options, {"confirm_overlap": True})
        self.assertTrue(following.to_json()["confirm_overlap"])
        self.assertEqual((await self.queue.latest(self.kb.id))[source].id, following.id)
        self.assertEqual(self.source_status(source), "processing")
        extract = await self.queue.claim("extract", "w2")
        self.assertIsNone(await self.queue.complete(extract, "w2"))
        self.assertEqual((await self.queue.latest(self.kb.id, [source]))[source].status, "done")
        self.assertEqual(self.source_status(source), "processed")
        self.assertEqual(await self.queue.latest(self.kb.id, []), {})
        self.assertEqual(await self.queue.latest(self.add_kb().id), {})

    async def test_one_active_job_per_source(self) -> None:
        source, other = await self.add_source(), await self.add_source()
        await self.queue.enqueue(self.kb.id, source, "extract", mode="knowledge")
        with self.assertRaises(JobConflict):
            await self.queue.enqueue(self.kb.id, source, "convert")
        await self.queue.enqueue(self.kb.id, other, "extract", mode="knowledge")
        with self.assertRaises(ValueError):
            await self.queue.enqueue(self.kb.id, source, "ocr")
        # The source must belong to the job's knowledge base.
        fresh = await self.add_source()
        with self.assertRaises(InactiveSource):
            await self.queue.enqueue(self.add_kb().id, fresh, "convert")

    async def test_retry_with_backoff_then_fail(self) -> None:
        source = await self.add_source()
        await self.queue.enqueue(self.kb.id, source, "extract", mode="knowledge")
        job = await self.queue.claim("extract", "w1")
        retried = await self.queue.fail(job, "w1", "service down", retry=True)
        self.assertEqual((retried.status, retried.error, retried.to_json()["retry_at"] is not None),
                         ("queued", "service down", True))
        self.assertEqual(self.source_status(source), "processing")
        for attempt in range(2, 4):
            job = await self.queue.claim("extract", "w1")
            self.assertEqual((job.attempts, job.error), (attempt, None))
            job = await self.queue.fail(job, "w1", "service down", retry=True)
        self.assertEqual(job.status, "failed")
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(self.source_status(source), "failed")
        # A failed job no longer blocks a new request for the source.
        await self.queue.enqueue(self.kb.id, source, "extract", mode="documents")

    async def test_permanent_failure_does_not_retry(self) -> None:
        source = await self.add_source()
        await self.queue.enqueue(self.kb.id, source, "convert")
        job = await self.queue.claim("convert", "w1")
        failed = await self.queue.fail(job, "w1", "x" * 3000, retry=False)
        self.assertEqual((failed.status, failed.attempts, len(failed.error)), ("failed", 1, 2000))
        self.assertIsNone(await self.queue.claim("convert", "w1"))
        self.assertEqual(self.source_status(source), "failed")

    async def test_lease_is_checked_and_stale_jobs_are_recovered(self) -> None:
        source = await self.add_source()
        await self.queue.enqueue(self.kb.id, source, "extract", mode="documents")
        job = await self.queue.claim("extract", "w1")
        self.assertTrue(await self.queue.heartbeat(job, "w1"))
        self.assertFalse(await self.queue.heartbeat(job, "intruder"))
        with self.assertRaises(JobLeaseLost):
            await self.queue.complete(job, "intruder")
        self.assertEqual(await self.queue.recover_stale(3600), [])
        for attempt in (1, 2, 3):
            await asyncio.sleep(0.05)
            (recovered,) = await self.queue.recover_stale(0.01)
            expected = "queued" if attempt < 3 else "failed"
            self.assertEqual((recovered.id, recovered.status, recovered.error), (job.id, expected, STALE_ERROR))
            if attempt < 3:
                self.assertFalse(await self.queue.heartbeat(job, "w1"))
                job = await self.queue.claim("extract", f"w{attempt + 1}")
                self.assertEqual(job.attempts, attempt + 1)
        self.assertIsNotNone(recovered.finished_at)
        self.assertEqual(self.source_status(source), "failed")

    async def test_release_returns_the_attempt(self) -> None:
        source = await self.add_source()
        await self.queue.enqueue(self.kb.id, source, "extract", mode="documents")
        job = await self.queue.claim("extract", "w1")
        await self.queue.release(job, "w1")
        await self.queue.release(job, "w1")  # a lost lease is ignored
        again = await self.queue.claim("extract", "w1")
        self.assertEqual(again.attempts, 1)

    async def test_concurrent_claims_never_share_a_job(self) -> None:
        for _ in range(10):
            await self.queue.enqueue(self.kb.id, await self.add_source(), "extract", mode="documents")
        claims = await asyncio.gather(*(self.queue.claim("extract", f"w{index}") for index in range(15)))
        claimed = [job.id for job in claims if job is not None]
        self.assertEqual((len(claimed), len(set(claimed))), (10, 10))

    async def test_notification_wakes_a_listening_worker(self) -> None:
        listener = self.queue.listener(self.database.url, ["extract"])
        try:
            self.assertFalse(await listener.wait(0.05))
            waiting = asyncio.create_task(listener.wait(5))
            await asyncio.sleep(0.2)  # the LISTEN is in place
            await self.queue.enqueue(self.kb.id, await self.add_source(), "extract", mode="documents")
            self.assertTrue(await waiting)
        finally:
            await listener.aclose()


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class ExtractionRunRepositoryTest(RepositoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.runs = ExtractionRunRepository(self.sessions, self.kb.id)
        self.signatures = ContentSignatureRepository(self.sessions, self.kb.id)

    async def start(self, source_id, content=H_C1, mode="knowledge", fingerprint="f1", **options):
        return await self.runs.start(source_id=source_id, content=content, mode=mode,
                                     fingerprint=fingerprint, settings={"v": fingerprint}, **options)

    async def job(self, source_id: UUID) -> UUID:
        queue = IngestionJobRepository(self.sessions)
        job = await queue.enqueue(self.kb.id, source_id, "extract", mode="knowledge")
        with self.engine.begin() as conn:
            conn.execute(update(IngestionJob).where(IngestionJob.id == job.id).values(status="done"))
        return job.id

    async def test_one_active_run_per_content_and_mode_within_a_kb(self) -> None:
        a, b = await self.add_source(), await self.add_source()
        run = await self.start(a, original_sha256="b" * 64, job_id=await self.job(a))
        with self.assertRaises(RunConflict) as conflict:
            await self.start(b)
        self.assertEqual(conflict.exception.run.id, run.id)
        with self.assertRaises(RunConflict):
            await self.start(b, fingerprint="f2")
        self.assertEqual((await self.start(b, mode="documents")).status, "running")
        self.assertEqual((await self.runs.find_active(H_C1, "knowledge", exclude=b)).id, run.id)
        self.assertIsNone(await self.runs.find_active(H_C1, "knowledge", exclude=a))
        self.assertEqual((await self.runs.find_active_original("b" * 64, "knowledge")).id, run.id)
        # Another knowledge base extracts the same content independently.
        other = self.add_kb()
        other_runs = ExtractionRunRepository(self.sessions, other.id)
        elsewhere = await other_runs.start(source_id=await self.add_source(other), content=H_C1,
                                           mode="knowledge", fingerprint="f1", settings={})
        self.assertEqual(elsewhere.status, "running")
        self.assertEqual((await self.runs.find_active(H_C1, "knowledge")).id, run.id)
        with self.assertRaises(IntegrityError):
            await other_runs.start(source_id=a, content=H_C9, mode="knowledge", fingerprint="f1", settings={})

    async def test_retried_job_takes_over_and_finished_runs_are_history(self) -> None:
        a, b = await self.add_source(), await self.add_source()
        first_job, second_job = await self.job(a), await self.job(a)
        run = await self.start(a, job_id=first_job)
        again = await self.start(a, job_id=second_job)
        self.assertEqual((again.id, again.job_id), (run.id, second_job))
        await self.runs.finish(run.id, "failed", error="boom")
        retried = await self.start(b)
        await self.runs.finish(retried.id, "done", result={"stored": 2})
        await self.runs.finish(retried.id, "failed")  # a finished run is not changed again
        with self.assertRaises(RunConflict) as conflict:
            await self.start(a)
        self.assertEqual((conflict.exception.run.status, conflict.exception.run.result),
                         ("done", {"stored": 2}))
        self.assertEqual([item.status for item in await self.runs.for_source(a)], ["failed"])

    async def test_stale_running_run_stops_blocking(self) -> None:
        a, b = await self.add_source(), await self.add_source()
        await self.start(a)
        await asyncio.sleep(0.01)
        self.runs.stale_after_s = 0
        self.assertIsNone(await self.runs.find_active(H_C1, "knowledge", exclude=b))
        run = await self.start(b)
        self.assertEqual(run.source_id, b)
        (abandoned,) = await self.runs.for_source(a)
        self.assertEqual((abandoned.status, abandoned.error), ("failed", ABANDONED))

    async def test_overlap_candidates_share_bands_and_have_a_live_run_in_the_mode(self) -> None:
        a, b, c = await self.add_source(), await self.add_source(), await self.add_source()
        run = await self.start(a, content=H_C1)
        await self.start(c, content=H_C3, mode="documents")
        for source, content, keys in ((a, H_C1, ["0:x", "1:y", "2:z"]), (b, H_C2, ["0:x"]),
                                      (c, H_C3, ["0:x", "1:y"])):
            await self.signatures.put(source, content=content, version="v1",
                                      shingle_count=len(keys), bands=keys)

        async def candidates(mode="knowledge", runs=self.runs, **options):
            options = {"version": "v1", "content": H_NEW, **options}
            found = await runs.overlap_candidates(["0:x", "1:y", "9:q"], mode, **options)
            return [(item.source_id, hits) for item, hits in found]

        # b has no run: it was never extracted, so it is not compared.
        self.assertEqual(await candidates(), [(a, 2)])
        self.assertEqual(await candidates("documents"), [(c, 2)])
        self.assertEqual(await candidates(content=H_C1), [])
        self.assertEqual(await candidates(exclude=a), [])
        self.assertEqual(await candidates(version="v2"), [])
        self.assertEqual(await candidates(runs=ExtractionRunRepository(self.sessions, self.add_kb().id)), [])
        self.assertEqual(await self.signatures.unsigned("v1"), [])
        self.assertEqual(set(await self.signatures.unsigned("v2")), {a, c})
        await self.signatures.put(a, content=H_C1, version="v2", shingle_count=1, bands=["0:x"])
        self.assertEqual(await self.signatures.unsigned("v2"), [c])
        await self.runs.finish(run.id, "failed")
        self.assertEqual(await candidates(version="v2"), [])


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class ConflictRepositoryTest(RepositoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.conflicts = ConflictRepository(self.sessions, self.kb.id)
        source = await self.add_source()
        records = [record(), record(), record()]
        await ChunkRepository(self.sessions).add_records(self.kb.id, source, records)
        self.ids = sorted((item.id for item in records), key=str)

    def row(self, a: UUID, b: UUID, **values) -> ConflictRow:
        return ConflictRow(**{"a_id": a, "b_id": b, "a_hash": "h1", "b_hash": "h1",
                              "judge_fingerprint": "j1", "status": "open", **values})

    async def test_pairs_are_ordered_in_the_database(self) -> None:
        a, b = self.ids[:2]
        self.assertEqual(ordered(b, a), (a, b))
        with self.engine.begin() as conn, self.assertRaises(IntegrityError):
            conn.execute(text(
                "INSERT INTO knowledge_conflicts (id, knowledge_base_id, a_id, b_id, a_hash, b_hash, "
                "judge_fingerprint, status) VALUES (:id, :kb, :b, :a, 'h', 'h', 'j', 'open')"
            ), {"id": uuid4(), "kb": self.kb.id, "a": a, "b": b})

    async def test_reviews_append_and_hold_until_the_content_changes(self) -> None:
        a, b, c = self.ids
        stored = await self.conflicts.put(self.row(a, b, quotes={"a": "đúng"}))
        self.assertEqual((stored.status, stored.quotes, stored.review), ("open", {"a": "đúng"}, []))
        # A machine verdict is replaced in place.
        stored = await self.conflicts.put(self.row(a, b, status="pending"))
        reviewed = await self.conflicts.set_review(stored.id, "dismissed", linked=False,
                                                   event={"action": "dismiss", "by": "u1"})
        reviewed = await self.conflicts.set_review(stored.id, "confirmed", linked=True,
                                                   event={"action": "confirm", "by": "u1"})
        self.assertEqual([entry["action"] for entry in reviewed.review], ["dismiss", "confirm"])
        kept = await self.conflicts.put(self.row(a, b, status="no_conflict"))
        self.assertEqual((kept.id, kept.status, kept.linked), (stored.id, "confirmed", True))
        # Another judge fingerprint on the same content does not hide the review.
        await self.conflicts.put(self.row(a, b, judge_fingerprint="j2", status="no_conflict"))
        self.assertEqual((await self.conflicts.current([(a, b)]))[(a, b)].status, "confirmed")
        # An edit reopens the pair; the review history stays.
        reopened = await self.conflicts.put(self.row(a, b, b_hash="h2", status="open"))
        self.assertEqual((reopened.id, reopened.status, len(reopened.review)), (stored.id, "open", 2))
        self.assertEqual(set(await self.conflicts.get([(a, b), (a, c)], "j1")), {(a, b)})
        self.assertEqual((await self.conflicts.get_row(stored.id)).b_hash, "h2")
        other = ConflictRepository(self.sessions, self.add_kb().id)
        self.assertIsNone(await other.get_row(stored.id))
        self.assertIsNone(await other.set_review(stored.id, "dismissed", linked=False, event={}))
        with self.assertRaises(ValueError):
            await self.conflicts.set_review(stored.id, "approved", linked=False, event={})

    async def test_listing_shows_current_rows_and_counts(self) -> None:
        a, b, c = self.ids
        await self.conflicts.put(self.row(a, b))
        await self.conflicts.put(self.row(a, c, status="no_conflict"))
        await self.conflicts.put(self.row(b, c, status="needs_review"))
        rows, counts = await self.conflicts.list_rows(statuses=["open", "needs_review"], limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(counts, {"open": 1, "no_conflict": 1, "needs_review": 1})
        rows, counts = await self.conflicts.list_rows(record_ids=[c])
        self.assertEqual({row.pair for row in rows}, {(a, c), (b, c)})
        self.assertEqual(counts, {"no_conflict": 1, "needs_review": 1})
        found = await self.conflicts.for_records([a], ["open"])
        self.assertEqual([row.pair for row in found], [(a, b)])
        self.assertEqual(await self.conflicts.for_records([], []), [])

    async def test_pairs_of_another_kb_and_job_links_are_checked(self) -> None:
        a, b = self.ids[:2]
        source = await self.add_source()
        job = await IngestionJobRepository(self.sessions).enqueue(self.kb.id, source, "conflict")
        stored = await self.conflicts.put(self.row(a, b, job_id=str(job.id), new_id=b))
        self.assertEqual((stored.job_id, stored.new_id), (str(job.id), b))
        with self.engine.begin() as conn:
            conn.execute(update(IngestionJob).where(IngestionJob.id == job.id).values(status="done"))
        with self.assertRaises(IntegrityError):
            await ConflictRepository(self.sessions, self.add_kb().id).put(self.row(a, b))


if __name__ == "__main__":
    unittest.main()

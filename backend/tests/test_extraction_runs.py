"""Extraction run fingerprints and the run store contract (memory, and PostgreSQL when configured).

    TEST_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent \
        uv run python -m unittest backend.tests.test_extraction_runs -v

The PostgreSQL tests use a throwaway schema, so existing data is untouched.
"""

from __future__ import annotations

import os
import unittest
from datetime import timedelta
from dataclasses import replace
from uuid import uuid4

from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlalchemy import text

from backend.src.storage import ConversationStatus, KnowledgeStore
from backend.src.storage.extraction_runs import (
    LEGACY_FINGERPRINT,
    ExtractionRunStore,
    MemoryExtractionRunStore,
    RunConflict,
    content_sha256,
    extraction_fingerprint,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class FingerprintTest(unittest.TestCase):
    def test_content_ignores_whitespace_and_unicode_form_only(self):
        composed = "Trục Z kêu.\r\n  Chưa rõ nguyên nhân. "
        decomposed = "Trục Z kêu. Chưa rõ nguyên nhân."
        self.assertEqual(content_sha256(composed), content_sha256(decomposed))
        self.assertNotEqual(content_sha256("Trục Z kêu."), content_sha256("trục z kêu."))
        self.assertNotEqual(content_sha256("Trục Z kêu."), content_sha256("Trục Z kêu"))

    def test_extraction_fingerprint_covers_mode_and_settings(self):
        settings = {"prompt_sha256": "p1", "model": "m"}
        self.assertEqual(extraction_fingerprint("knowledge", settings),
                         extraction_fingerprint("knowledge", dict(reversed(settings.items()))))
        self.assertNotEqual(extraction_fingerprint("knowledge", settings),
                            extraction_fingerprint("documents", settings))
        self.assertNotEqual(extraction_fingerprint("knowledge", settings),
                            extraction_fingerprint("knowledge", {**settings, "prompt_sha256": "p2"}))


class RunStoreContract:
    """Shared by the memory and PostgreSQL stores; `evidence()` creates a row id."""

    async def evidence(self):
        return uuid4()

    async def start(self, evidence_id, content="c1", mode="knowledge", fingerprint="f1", **options):
        return await self.runs.start(evidence_id=evidence_id, content=content, mode=mode,
                                     fingerprint=fingerprint, settings={"v": fingerprint}, **options)

    async def test_one_active_run_per_content_and_mode(self):
        a, b = await self.evidence(), await self.evidence()
        run = await self.start(a, original_sha256="o1", job_id="j1")
        with self.assertRaises(RunConflict) as conflict:
            await self.start(b)
        self.assertEqual(conflict.exception.run.id, run.id)
        # Older logic still blocks in phase 1; re-extraction is not implemented.
        with self.assertRaises(RunConflict):
            await self.start(b, fingerprint="f2")
        self.assertEqual((await self.start(b, mode="documents")).status, "running")
        self.assertEqual((await self.runs.find_active("c1", "knowledge", exclude=b)).id, run.id)
        self.assertIsNone(await self.runs.find_active("c1", "knowledge", exclude=a))
        self.assertEqual((await self.runs.find_active_original("o1", "knowledge")).id, run.id)

    async def test_retried_job_takes_over_and_finished_runs_are_history(self):
        a, b = await self.evidence(), await self.evidence()
        run = await self.start(a, job_id="j1")
        again = await self.start(a, job_id="j2")
        self.assertEqual((again.id, again.job_id), (run.id, "j2"))
        await self.runs.finish(run.id, "failed", error="boom")
        retried = await self.start(b)
        await self.runs.finish(retried.id, "done", result={"stored": 2})
        await self.runs.finish(retried.id, "failed")  # a finished run is not changed again
        with self.assertRaises(RunConflict) as conflict:
            await self.start(a)
        self.assertEqual((conflict.exception.run.status, conflict.exception.run.result),
                         ("done", {"stored": 2}))
        self.assertEqual([item.status for item in await self.runs.for_evidence(a)], ["failed"])

    async def test_stale_running_run_stops_blocking(self):
        a, b = await self.evidence(), await self.evidence()
        await self.start(a)
        self.runs.stale_after_s = 0
        self.assertIsNone(await self.runs.find_active("c1", "knowledge", exclude=b))
        run = await self.start(b)
        self.assertEqual(run.evidence_id, b)
        (abandoned,) = await self.runs.for_evidence(a)
        self.assertEqual(abandoned.status, "failed")

    async def test_legacy_extractions_are_recorded_as_done_or_duplicate(self):
        a, b = await self.evidence(), await self.evidence()
        first = await self.runs.record_legacy(evidence_id=a, content="c1", mode="knowledge",
                                              fingerprint=LEGACY_FINGERPRINT, settings={})
        second = await self.runs.record_legacy(evidence_id=b, content="c1", mode="knowledge",
                                               fingerprint=LEGACY_FINGERPRINT, settings={})
        self.assertEqual((first.status, second.status), ("done", "duplicate"))
        self.assertEqual((await self.runs.find_active("c1", "knowledge")).id, first.id)

    async def test_overlap_candidates_share_bands_and_have_a_live_run_in_the_mode(self):
        a, b, c = await self.evidence(), await self.evidence(), await self.evidence()
        run = await self.start(a, content="c1")
        await self.start(c, content="c3", mode="documents")
        for evidence, content, keys in ((a, "c1", ["0:x", "1:y", "2:z"]), (b, "c2", ["0:x"]),
                                        (c, "c3", ["0:x", "1:y"])):
            await self.runs.put_signature(evidence, content=content, version="v1",
                                          shingle_count=len(keys), bands=keys)

        async def candidates(mode="knowledge", **options):
            options = {"version": "v1", "content": "new", **options}
            found = await self.runs.overlap_candidates(["0:x", "1:y", "9:q"], mode, **options)
            return [(item.evidence_id, hits) for item, hits in found]

        # b has no run: it was never extracted, so it is not compared.
        self.assertEqual(await candidates(), [(a, 2)])
        self.assertEqual(await candidates("documents"), [(c, 2)])
        self.assertEqual(await candidates(content="c1"), [])  # exact duplicates are reported apart
        self.assertEqual(await candidates(exclude=a), [])
        self.assertEqual(await candidates(version="v2"), [])
        self.assertEqual(await self.runs.unsigned("v1"), [])
        self.assertEqual(set(await self.runs.unsigned("v2")), {a, c})
        await self.runs.finish(run.id, "failed")
        self.assertEqual(await candidates(), [])


class MemoryRunStoreTest(RunStoreContract, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runs = MemoryExtractionRunStore()

    async def test_stale_running_run_stops_blocking(self):
        a = await self.evidence()
        run = await self.start(a)
        # Memory runs compare timestamps; age the run instead of waiting.
        self.runs.runs[run.id] = replace(run, updated_at=run.updated_at - timedelta(hours=2))
        b = await self.evidence()
        self.assertIsNone(await self.runs.find_active("c1", "knowledge", exclude=b))
        self.assertEqual((await self.start(b)).evidence_id, b)
        self.assertEqual([item.status for item in await self.runs.for_evidence(a)], ["failed"])


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class PostgresRunStoreTest(RunStoreContract, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"test_{uuid4().hex[:12]}"
        self.store = KnowledgeStore.from_url(DATABASE_URL, DeterministicFakeEmbedding(size=16), 16,
                                             schema=self.schema)
        async with self.store.engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA {self.schema}"))
        await self.store.init_schema()
        self.runs = ExtractionRunStore(self.store.engine, schema=self.store.schema)
        await self.runs.init_schema()
        await self.runs.init_schema()  # idempotent

    async def asyncTearDown(self):
        async with self.store.engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        await self.store.aclose()

    async def evidence(self):
        return (await self.store.add_conversation("text")).id

    async def test_unique_index_rejects_a_second_active_run_of_the_same_logic(self):
        a, b = await self.evidence(), await self.evidence()
        await self.start(a)
        async with self.store.engine.begin() as conn:
            with self.assertRaises(Exception):
                await conn.execute(text(
                    f'INSERT INTO "{self.schema}".extraction_runs (id, evidence_id, content_sha256, mode, '
                    "fingerprint, status) VALUES (:id, :evidence, 'c1', 'knowledge', 'f1', 'done')"
                ), {"id": uuid4(), "evidence": b})

    async def test_unrecorded_lists_extracted_rows_without_runs(self):
        extracted = await self.store.add_conversation("old")
        await self.store.mark_conversation(extracted.id, ConversationStatus.PROCESSED)
        pending = await self.store.add_conversation("new")
        self.assertEqual([row.id for row in await self.runs.unrecorded()], [extracted.id])
        await self.runs.record_legacy(evidence_id=extracted.id, content="c", mode="knowledge",
                                      fingerprint=LEGACY_FINGERPRINT, settings={})
        self.assertEqual(await self.runs.unrecorded(), [])
        self.assertNotIn(pending.id, [row.id for row in await self.runs.unrecorded()])


if __name__ == "__main__":
    unittest.main()

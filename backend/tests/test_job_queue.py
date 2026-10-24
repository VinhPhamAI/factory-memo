"""Stage job queue and workers: the in-memory contract offline, PostgreSQL when available.

    TEST_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent \
      uv run python -m unittest backend.tests.test_job_queue -v

The PostgreSQL tests use a throwaway schema (and its own notification channels).
"""

import asyncio
import logging
import os
import unittest
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.src.services.job_worker import StageWorker
from backend.src.services.source_pipeline import JobFailed
from backend.src.storage.job_queue import JobConflict, JobLeaseLost, JobQueue, MemoryJobQueue, retry_delay


DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class FakePipeline:
    """Records the jobs it runs; `behaviour[stage]` decides each outcome."""

    def __init__(self, behaviour=None):
        self.behaviour = behaviour or {}
        self.ran = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, job):
        self.ran.append((job.stage, job.source_id, job.mode, job.attempts))
        self.started.set()
        action = self.behaviour.get(job.stage)
        if action == "block":
            await self.release.wait()
        elif isinstance(action, BaseException):
            raise action
        return {"stage": job.stage}


class QueueContract:
    """Shared by the in-memory and PostgreSQL queues."""

    queue: object

    async def test_enqueue_claim_and_chain_the_next_stage(self):
        job = await self.queue.enqueue("general", "convert", "upload", "a1", mode="documents",
                                       next_stages=["extract"], options={"confirm_overlap": True})
        self.assertEqual((job.status, job.attempts, job.options), ("queued", 0, {"confirm_overlap": True}))
        self.assertIsNone(await self.queue.claim("extract", "w1"))
        claimed = await self.queue.claim("convert", "w1")
        self.assertEqual((claimed.id, claimed.status, claimed.attempts, claimed.locked_by),
                         (job.id, "running", 1, "w1"))
        self.assertIsNone(await self.queue.claim("convert", "w2"))
        following = await self.queue.complete(claimed, "w1", {"converter": "text"})
        self.assertEqual((following.stage, following.mode, following.parent_id, following.next_stages),
                         ("extract", "documents", job.id, ()))
        # The reviewer's options apply to the whole pipeline.
        self.assertEqual(following.options, {"confirm_overlap": True})
        self.assertTrue(following.to_json()["confirm_overlap"])
        latest = await self.queue.latest("general")
        self.assertEqual(latest[("upload", "a1")].id, following.id)
        extract = await self.queue.claim("extract", "w2")
        self.assertIsNone(await self.queue.complete(extract, "w2"))
        latest = await self.queue.latest("general", [("upload", "a1")])
        self.assertEqual(latest[("upload", "a1")].status, "done")
        self.assertEqual(await self.queue.latest("general", []), {})

    async def test_one_active_job_per_source(self):
        await self.queue.enqueue("general", "extract", "conversation", "c1", mode="knowledge")
        with self.assertRaises(JobConflict):
            await self.queue.enqueue("general", "convert", "conversation", "c1")
        # Another KB or source is independent.
        await self.queue.enqueue("industrial_qa", "extract", "conversation", "c1", mode="knowledge")
        with self.assertRaises(ValueError):
            await self.queue.enqueue("general", "ocr", "upload", "x")

    async def test_retry_with_backoff_then_fail(self):
        await self.queue.enqueue("general", "extract", "upload", "u1", mode="knowledge")
        job = await self.queue.claim("extract", "w1")
        retried = await self.queue.fail(job, "w1", "service down", retry=True)
        self.assertEqual((retried.status, retried.error), ("queued", "service down"))
        self.assertEqual(retry_delay(1, 30), 30)
        self.assertEqual(retry_delay(3, 30), 120)
        self.assertEqual(retry_delay(20, 30), 3600)
        for attempt in range(2, 4):
            job = await self.queue.claim("extract", "w1")
            self.assertEqual(job.attempts, attempt)
            self.assertIsNone(job.error)
            job = await self.queue.fail(job, "w1", "service down", retry=True)
        self.assertEqual(job.status, "failed")
        self.assertIsNotNone(job.finished_at)
        # A failed job no longer blocks a new request for the source.
        await self.queue.enqueue("general", "extract", "upload", "u1", mode="documents")

    async def test_permanent_failure_does_not_retry(self):
        await self.queue.enqueue("general", "convert", "upload", "u2")
        job = await self.queue.claim("convert", "w1")
        failed = await self.queue.fail(job, "w1", "no converter", retry=False)
        self.assertEqual((failed.status, failed.attempts), ("failed", 1))
        self.assertIsNone(await self.queue.claim("convert", "w1"))

    async def test_lease_is_checked_and_stale_jobs_are_recovered(self):
        await self.queue.enqueue("general", "extract", "upload", "u3", mode="documents")
        job = await self.queue.claim("extract", "w1")
        self.assertTrue(await self.queue.heartbeat(job, "w1"))
        self.assertFalse(await self.queue.heartbeat(job, "intruder"))
        with self.assertRaises(JobLeaseLost):
            await self.queue.complete(job, "intruder")
        self.assertEqual(await self.queue.recover_stale(3600), [])
        await asyncio.sleep(0.05)
        recovered = await self.queue.recover_stale(0.01)
        self.assertEqual([(item.id, item.status) for item in recovered], [(job.id, "queued")])
        self.assertFalse(await self.queue.heartbeat(job, "w1"))
        again = await self.queue.claim("extract", "w2")
        self.assertEqual((again.id, again.attempts), (job.id, 2))

    async def test_release_returns_the_attempt(self):
        await self.queue.enqueue("general", "extract", "upload", "u4", mode="documents")
        job = await self.queue.claim("extract", "w1")
        await self.queue.release(job, "w1")
        again = await self.queue.claim("extract", "w1")
        self.assertEqual(again.attempts, 1)

    async def test_notification_wakes_a_listening_worker(self):
        listener = self.queue.listener(["extract"])
        try:
            self.assertFalse(await listener.wait(0.05))
            waiting = asyncio.create_task(listener.wait(5))
            await asyncio.sleep(0.2)  # the LISTEN is in place
            await self.queue.enqueue("general", "extract", "upload", "u5", mode="documents")
            self.assertTrue(await waiting)
        finally:
            await listener.aclose()

    async def test_worker_runs_the_pipeline_in_order(self):
        pipeline = FakePipeline()
        convert = StageWorker(self.queue, pipeline, ["convert"], name="c", poll_s=5)
        extract = StageWorker(self.queue, pipeline, ["extract"], name="e", poll_s=5)
        stop = asyncio.Event()
        tasks = [asyncio.create_task(worker.run(stop)) for worker in (convert, extract)]
        try:
            await asyncio.sleep(0.2)
            await self.queue.enqueue("general", "convert", "upload", "u6", mode="knowledge",
                                     next_stages=["extract"])
            for _ in range(100):
                if len(pipeline.ran) == 2:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(pipeline.ran, [("convert", "u6", "knowledge", 1), ("extract", "u6", "knowledge", 1)])
        finally:
            stop.set()
            await asyncio.gather(*tasks)

    async def test_stopping_hands_an_unfinished_job_back(self):
        pipeline = FakePipeline({"extract": "block"})
        worker = StageWorker(self.queue, pipeline, ["extract"], name="w", poll_s=5, grace_s=0.05)
        job = await self.queue.enqueue("general", "extract", "upload", "u7", mode="documents")
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        await asyncio.wait_for(pipeline.started.wait(), 5)
        stop.set()
        await asyncio.wait_for(task, 5)
        again = await self.queue.claim("extract", "other")
        self.assertEqual((again.id, again.attempts), (job.id, 1))


class MemoryJobQueueTest(QueueContract, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue = MemoryJobQueue(retry_delay_s=0)
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)

    async def test_backoff_delays_the_next_claim(self):
        queue = MemoryJobQueue(retry_delay_s=60)
        await queue.enqueue("general", "extract", "upload", "u8", mode="documents")
        job = await queue.claim("extract", "w1")
        retried = await queue.fail(job, "w1", "down", retry=True)
        self.assertGreaterEqual(retried.run_after - retried.created_at, timedelta(seconds=59))
        self.assertIsNone(await queue.claim("extract", "w1"))
        self.assertIsNotNone(retried.to_json()["retry_at"])

    async def test_failure_classification_in_the_worker(self):
        pipeline = FakePipeline({"convert": JobFailed("Chưa có OCR"), "extract": ConnectionError("down")})
        worker = StageWorker(self.queue, pipeline, ["convert", "extract"], name="w")
        convert = await self.queue.enqueue("general", "convert", "upload", "u9", next_stages=["extract"])
        extract = await self.queue.enqueue("general", "extract", "conversation", "c9", mode="knowledge")
        await worker.drain()
        self.assertEqual((self.queue.jobs[convert.id].status, self.queue.jobs[convert.id].attempts,
                          self.queue.jobs[convert.id].error), ("failed", 1, "Chưa có OCR"))
        self.assertEqual((self.queue.jobs[extract.id].status, self.queue.jobs[extract.id].attempts),
                         ("failed", 3))
        self.assertEqual(len(self.queue.jobs), 2)  # the failed convert queued no extract

    async def test_a_lost_lease_cancels_the_work(self):
        pipeline = FakePipeline({"extract": "block"})
        worker = StageWorker(self.queue, pipeline, ["extract"], name="w", heartbeat_s=0.02)
        job = await self.queue.enqueue("general", "extract", "upload", "u10", mode="documents")
        claimed = await self.queue.claim("extract", "w")
        running = asyncio.create_task(worker.process(claimed))
        await asyncio.wait_for(pipeline.started.wait(), 5)
        # Another worker's recovery took the job over.
        self.queue.jobs[job.id] = replace(self.queue.jobs[job.id], status="queued", locked_by=None)
        self.assertIsNone(await asyncio.wait_for(running, 5))
        self.assertEqual(self.queue.jobs[job.id].status, "queued")


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class PostgresJobQueueTest(QueueContract, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.schema = f"test_{uuid4().hex[:12]}"
        self.admin = create_async_engine(DATABASE_URL)
        async with self.admin.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA {self.schema}"))
        self.queue = JobQueue(DATABASE_URL, schema=self.schema, max_attempts=3, retry_delay_s=0)

    async def asyncTearDown(self):
        await self.queue.aclose()
        async with self.admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        await self.admin.dispose()

    async def test_concurrent_claims_never_share_a_job(self):
        for index in range(20):
            await self.queue.enqueue("general", "extract", "upload", f"p{index}", mode="documents")
        claims = await asyncio.gather(*(self.queue.claim("extract", f"w{index}") for index in range(30)))
        claimed = [job.id for job in claims if job is not None]
        self.assertEqual(len(claimed), 20)
        self.assertEqual(len(set(claimed)), 20)

    async def test_schema_creation_is_idempotent_across_processes(self):
        other = JobQueue(DATABASE_URL, schema=self.schema)
        try:
            await asyncio.gather(other.latest("general"), self.queue.latest("general"))
        finally:
            await other.aclose()


if __name__ == "__main__":
    unittest.main()

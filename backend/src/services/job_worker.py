"""Consumer loop for one or more job stages (see `backend.src.storage.job_queue`).

A worker waits for a notification on its stages' channels (or the poll
interval), claims one job, runs it through `SourcePipeline`, and completes or
fails it. Completing a job queues its next stage and notifies that stage's
workers. While a job runs, a heartbeat keeps the lease; if the lease is lost
(the job was recovered as stale), the job's work is cancelled.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any

from backend.src.services.source_pipeline import JobFailed, SourcePipeline, failure
from backend.src.storage.job_queue import Job, JobLeaseLost
from backend.src.storage.knowledge_store import StorageError
from backend.src.storage.repositories.base import KnowledgeStorageUnavailable
from backend.src.storage.repositories.ingestion_jobs import PreparedJobResult


logger = logging.getLogger(__name__)


def worker_id(stages) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{'+'.join(stages)}"


class StageWorker:
    def __init__(self, queue, pipeline: SourcePipeline, stages, *, name: str | None = None,
                 poll_s: float = 15.0, heartbeat_s: float = 15.0, stale_s: float = 180.0,
                 grace_s: float = 90.0) -> None:
        self.queue = queue
        self.pipeline = pipeline
        self.stages = tuple(stages)
        self.name = name or worker_id(self.stages)
        self.poll_s = poll_s
        self.heartbeat_s = heartbeat_s
        self.stale_s = stale_s
        self.grace_s = grace_s

    async def run(self, stop: asyncio.Event) -> None:
        """Processes jobs until `stop` is set; the current job may finish within the grace period."""

        listener = self.queue.listener(self.stages)
        logger.info("Worker %s listening on %s", self.name, ", ".join(self.stages))
        try:
            while not stop.is_set():
                try:
                    await self.queue.recover_stale(self.stale_s)
                    job = await self._claim()
                except (StorageError, KnowledgeStorageUnavailable):
                    logger.warning("Job queue unavailable; retrying in %ss", self.poll_s, exc_info=True)
                    await _wait(stop, self.poll_s)
                    continue
                if job is None:
                    # A notification or the poll interval; the next claim decides.
                    waiter = asyncio.create_task(listener.wait(self.poll_s))
                    stopper = asyncio.create_task(stop.wait())
                    await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
                    for task in (waiter, stopper):
                        task.cancel()
                    await asyncio.gather(waiter, stopper, return_exceptions=True)
                    continue
                await self._supervised(job, stop)
        finally:
            await listener.aclose()

    async def _claim(self) -> Job | None:
        for stage in self.stages:
            job = await self.queue.claim(stage, self.name)
            if job is not None:
                return job
        return None

    async def _supervised(self, job: Job, stop: asyncio.Event) -> None:
        task = asyncio.create_task(self.process(job))
        stopper = asyncio.create_task(stop.wait())
        await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        if not task.done():
            logger.info("Stopping: waiting up to %ss for job %s", self.grace_s, job.id)
            done, _ = await asyncio.wait({task}, timeout=self.grace_s)
            if not done:
                task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def drain(self) -> int:
        """Runs queued jobs until none is ready (tests and one-shot runs)."""

        count = 0
        while (job := await self._claim()) is not None:
            await self.process(job)
            count += 1
        return count

    async def process(self, job: Job) -> Job | None:
        """Runs one claimed job and records the outcome; returns the next-stage job."""

        logger.info(
            "Job %s: %s source %s (attempt %s/%s)",
            job.id, job.stage, job.source_id, job.attempts, job.max_attempts,
        )
        work = asyncio.create_task(self.pipeline.run(job))
        beat = asyncio.create_task(self._heartbeat(job, work))
        try:
            outcome: dict[str, Any] | PreparedJobResult = await work
            prepared = isinstance(outcome, PreparedJobResult)
            result = outcome.result if prepared else outcome
            options = {"persist": outcome.persist} if prepared else {}
            following = await self.queue.complete(
                job, self.name, result,
                continue_pipeline=not bool(result.pop("_terminal", False)),
                **options,
            )
        except JobLeaseLost:
            logger.warning("Job %s lost its lease before completion", job.id)
            return None
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if work.cancelled() and current is not None and not current.cancelling():
                # The heartbeat cancelled the work: the job was recovered as stale.
                logger.warning("Job %s lost its lease; abandoned", job.id)
                return None
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            await asyncio.shield(self._release(job))
            raise
        except Exception as exc:
            message, retry = failure(exc)
            # Expected failures carry their message; anything else needs its traceback.
            logger.warning("Job %s failed (attempt %s/%s, %s): %s", job.id, job.attempts,
                           job.max_attempts, "retry" if retry and job.attempts < job.max_attempts else "final", message,
                           exc_info=not isinstance(exc, JobFailed))
            try:
                await self.queue.fail(job, self.name, message, retry=retry)
            except JobLeaseLost:
                logger.warning("Job %s lost its lease before its failure was recorded", job.id)
            return None
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        if following is not None:
            logger.info("Job %s done; queued %s job %s", job.id, following.stage, following.id)
        else:
            logger.info("Job %s finished", job.id)
        return following

    async def _heartbeat(self, job: Job, work: asyncio.Task) -> bool:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            try:
                alive = await self.queue.heartbeat(job, self.name)
            except (StorageError, KnowledgeStorageUnavailable):
                logger.warning("Heartbeat for job %s failed; retrying", job.id)
                continue
            if not alive:
                work.cancel()
                return False

    async def _release(self, job: Job) -> None:
        try:
            await self.queue.release(job, self.name)
            logger.info("Job %s released back to the queue", job.id)
        except (StorageError, KnowledgeStorageUnavailable):
            logger.warning("Could not release job %s; it is requeued once stale", job.id)


async def _wait(stop: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout)
    except TimeoutError:
        pass

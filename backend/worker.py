"""Source processing workers: one supervisor, one process per stage slot.

    uv run python -m backend.worker                     # convert x1, extract x1
    uv run python -m backend.worker --convert 1 --extract 2
    uv run python -m backend.worker --stage extract     # a single worker, no supervisor

WORKER_CONVERT_PROCESSES and WORKER_EXTRACT_PROCESSES set the default counts.
Each child LISTENs on its stage's channel and claims jobs with SKIP LOCKED, so
workers may also run in several containers. The supervisor restarts a child
that exits unexpectedly and forwards SIGTERM/SIGINT; a child finishes its
current job within WORKER_SHUTDOWN_GRACE_S (default 90) seconds or hands it
back to the queue.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import multiprocessing
import os
import signal
import sys
import time

from backend.src.storage.job_queue import STAGES


logger = logging.getLogger("backend.worker")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _configure_logging() -> None:
    logging.basicConfig(
        level=(os.getenv("WORKER_LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s",
    )


async def _serve(stage: str) -> None:
    from backend.api.knowledge_bases import KnowledgeBaseCatalog
    from backend.knowledge_bases import KNOWLEDGE_BASES
    from backend.src.services.job_worker import StageWorker
    from backend.src.services.source_pipeline import SourcePipeline
    from backend.src.storage.document_uploads import DocumentUploadStore
    from backend.src.storage.job_queue import JobQueue

    class CatalogPipeline(SourcePipeline):
        """Resolves knowledge bases created in the UI after this process started."""

        async def run(self, job):
            if job.kb not in self.specs:
                try:
                    await KnowledgeBaseCatalog(KNOWLEDGE_BASES, listeners=[self]).ensure_loaded()
                except Exception:
                    logger.warning("Could not read the knowledge base registry", exc_info=True)
            return await super().run(job)

    queue = JobQueue()
    pipeline = CatalogPipeline(KNOWLEDGE_BASES, DocumentUploadStore())
    worker = StageWorker(
        queue, pipeline, [stage],
        poll_s=_env_float("WORKER_POLL_S", 15), heartbeat_s=_env_float("WORKER_HEARTBEAT_S", 15),
        stale_s=_env_float("WORKER_STALE_S", 180), grace_s=_env_float("WORKER_SHUTDOWN_GRACE_S", 90),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    try:
        await worker.run(stop)
    finally:
        await pipeline.aclose()
        await queue.aclose()


def run_stage(stage: str) -> None:
    """Child process entry point (also `--stage`)."""

    from dotenv import load_dotenv

    load_dotenv()
    _configure_logging()
    asyncio.run(_serve(stage))


def supervise(counts: dict[str, int]) -> int:
    context = multiprocessing.get_context("spawn")
    slots = [(stage, index) for stage in STAGES for index in range(counts.get(stage, 0))]
    if not slots:
        logger.error("No worker processes requested")
        return 2
    children: dict[tuple[str, int], multiprocessing.Process] = {}
    started: dict[tuple[str, int], float] = {}
    restarts: dict[tuple[str, int], int] = {}
    restart_at: dict[tuple[str, int], float] = {}
    stopping = False

    def start(slot):
        stage, index = slot
        process = context.Process(target=run_stage, args=(stage,), name=f"{stage}-{index}")
        process.start()
        children[slot] = process
        started[slot] = time.monotonic()
        logger.info("Started %s worker (pid %s)", process.name, process.pid)

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        for process in children.values():
            if process.is_alive() and process.pid:
                os.kill(process.pid, signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for slot in slots:
        start(slot)
    while True:
        alive = False
        for slot, process in list(children.items()):
            process.join(timeout=0.2)
            if process.is_alive():
                alive = True
                continue
            if stopping:
                continue
            if slot not in restart_at:
                # A child that ran for a while starts again quickly.
                if time.monotonic() - started[slot] > 300:
                    restarts[slot] = 0
                restarts[slot] = restarts.get(slot, 0) + 1
                delay = min(60, 2 ** min(restarts[slot], 6))
                restart_at[slot] = time.monotonic() + delay
                logger.warning("%s worker exited with %s; restarting in %ss",
                               process.name, process.exitcode, delay)
            if time.monotonic() >= restart_at[slot]:
                del restart_at[slot]
                start(slot)
            alive = True
        if stopping and not alive:
            return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage", choices=STAGES, help="run one worker in this process")
    for stage in STAGES:
        default = int(os.getenv(f"WORKER_{stage.upper()}_PROCESSES") or 1)
        parser.add_argument(f"--{stage}", type=int, default=default, metavar="N",
                            help=f"{stage} worker processes (default {default})")
    args = parser.parse_args(argv)
    _configure_logging()
    if args.stage:
        run_stage(args.stage)
        return 0
    return supervise({stage: max(0, getattr(args, stage)) for stage in STAGES})


if __name__ == "__main__":
    sys.exit(main())

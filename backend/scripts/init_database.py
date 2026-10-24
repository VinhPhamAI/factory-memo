"""Initialize shared API/worker tables without model calls or queued-job changes.

Run before the optional snapshot restore so database connectivity/DDL failures
cannot be mistaken for a best-effort seed failure. Per-KB schemas remain lazy.
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from backend.src.storage.databases import server_url_from_env
from backend.src.storage.job_queue import JobQueue
from backend.src.storage.kb_registry import KnowledgeBaseRegistry


async def initialize_database() -> None:
    registry = KnowledgeBaseRegistry.from_url(server_url_from_env())
    queue = JobQueue()
    try:
        await registry.list()
        # This read creates the queue schema but never claims or changes jobs.
        await queue.latest("industrial_qa")
    finally:
        try:
            await queue.aclose()
        finally:
            await registry.aclose()


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(initialize_database())
    print("Shared database tables ready: knowledge_base_registry, ingestion_jobs")

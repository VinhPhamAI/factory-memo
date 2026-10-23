"""The read-only near-duplicate report: pair scores offline, its query against PostgreSQL when configured.

    TEST_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent \
        uv run python -m unittest backend.tests.test_near_duplicate_report -v

The PostgreSQL test uses a throwaway schema, so existing data is untouched.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlalchemy import text

from backend.scripts.near_duplicate_report import EXACT, Source, fetch_sources, format_report, score_pairs
from backend.src.storage import KnowledgeStore
from backend.src.storage.content_signatures import CONTAINED, NEAR_DUPLICATE, Thresholds
from backend.src.storage.extraction_runs import ExtractionRunStore


DATABASE_URL = os.getenv("TEST_DATABASE_URL")


NOTE = ("Máy ép số 3 báo lỗi áp suất thủy lực thấp vào đầu ca đêm. Kỹ thuật viên kiểm tra mức dầu thấy "
        "bình thường, nghi van an toàn bị kẹt nên tháo ra vệ sinh, lắp lại và chạy thử hai mươi chu kỳ; "
        "áp suất ổn định ở mức một trăm bốn mươi bar nhưng chưa xác nhận nguyên nhân gốc.")
START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def source(evidence_id, text, *, days=0, mode="knowledge"):
    return Source(evidence_id, f"Ca {evidence_id}", mode, START + timedelta(days=days), text)


class ReportTest(unittest.TestCase):
    def test_levels_and_direction_of_each_pair(self):
        sources = [
            source("a", NOTE),
            source("b", NOTE + " Đã báo trưởng ca.", days=1),
            source("c", NOTE.split(";")[0], days=2),          # an excerpt of a
            source("d", NOTE, days=3),                          # identical to a
            source("e", NOTE, days=4, mode="documents"),        # other mode: not compared
            source("f", "Băng tải số 2 lệch về bên trái, đã chỉnh con lăn dẫn hướng.", days=5),
        ]
        pairs, compared = score_pairs(sources, Thresholds())
        self.assertEqual(compared, 10)  # 5 knowledge sources, the documents one alone
        levels = {(pair.newer.evidence_id, pair.older.evidence_id): pair.level for pair in pairs}
        self.assertEqual(levels[("b", "a")], NEAR_DUPLICATE)
        self.assertEqual(levels[("c", "a")], CONTAINED)  # the newer excerpt is inside the older note
        self.assertEqual(levels[("d", "a")], EXACT)
        self.assertNotIn(("e", "a"), levels)
        self.assertFalse(any("f" in key for key in levels))
        excerpt = next(pair for pair in pairs if pair.newer.evidence_id == "c" and pair.older.evidence_id == "a")
        self.assertEqual(excerpt.scores.containment, 1.0)
        self.assertLess(excerpt.scores.coverage, 0.9)
        self.assertTrue(all(pair.lsh for pair in pairs if pair.level == NEAR_DUPLICATE))

        report = format_report("general", sources, pairs, compared, Thresholds(), top=3)
        self.assertIn("Sources: documents 1, knowledge 5", report)
        self.assertIn("Levels: exact 1, near_duplicate", report)
        self.assertIn("«Ca d» [d] -> «Ca a» [a]", report)
        self.assertNotIn("van an toàn", report)  # names only, never the transcripts

    def test_no_sources(self):
        pairs, compared = score_pairs([], Thresholds())
        self.assertEqual((pairs, compared), ([], 0))
        self.assertIn("Sources: none", format_report("general", [], pairs, compared, Thresholds(), top=5))


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class FetchSourcesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"test_{uuid4().hex[:12]}"
        self.store = KnowledgeStore.from_url(DATABASE_URL, DeterministicFakeEmbedding(size=16), 16,
                                             schema=self.schema)
        async with self.store.engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA {self.schema}"))
        await self.store.init_schema()

    async def asyncTearDown(self):
        async with self.store.engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        await self.store.aclose()

    async def fetch(self, **options):
        async with self.store.engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL search_path TO {self.schema}"))
            sources = await fetch_sources(conn, **{"mode": None, "all_sources": False, "limit": 10, **options})
            await conn.rollback()
        return sorted((source.name, source.mode) for source in sources)

    async def test_extracted_sources_per_mode_or_all_rows(self):
        extracted = await self.store.add_conversation(NOTE, metadata={"name": "ca-dem.txt"})
        failed = await self.store.add_conversation(NOTE, metadata={"name": "loi.txt"})
        await self.store.add_conversation("Chưa trích xuất.", metadata={"name": "moi.txt"})
        # Before any run exists the report falls back to every evidence row.
        self.assertEqual(await self.fetch(), [("ca-dem.txt", "all"), ("loi.txt", "all"), ("moi.txt", "all")])
        runs = ExtractionRunStore(self.store.engine, schema=self.store.schema)
        await runs.init_schema()
        for mode in ("knowledge", "documents"):
            await runs.start(evidence_id=extracted.id, content=f"c-{mode}", mode=mode, fingerprint="f",
                             settings={})
        run = await runs.start(evidence_id=failed.id, content="c2", mode="knowledge", fingerprint="f",
                               settings={})
        await runs.finish(run.id, "failed")
        self.assertEqual(await self.fetch(), [("ca-dem.txt", "documents"), ("ca-dem.txt", "knowledge")])
        self.assertEqual(await self.fetch(mode="documents"), [("ca-dem.txt", "documents")])
        self.assertEqual(len(await self.fetch(all_sources=True)), 3)


if __name__ == "__main__":
    unittest.main()

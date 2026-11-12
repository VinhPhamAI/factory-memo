"""Focused offline checks for the application-database source pipeline."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock
from uuid import uuid4

from backend.src.services.application_source_pipeline import (
    ApplicationSourcePipeline,
    convert_plain_text,
)
from backend.src.services.demo.knowledge_extraction import (
    PDF_CONTENT_TYPE,
    load_demo_knowledge_extraction,
    read_demo_knowledge_extraction,
)
from backend.src.services.demo.scenario import DEMO_MODE_ENV
from backend.src.services.source_pipeline import NO_CONVERTER, UNREADABLE_TEXT, JobFailed
from backend.src.storage.repositories.ingestion_jobs import PreparedJobResult
from backend.src.storage.repositories.sources import FileEntry


class PlainTextConversionTest(unittest.TestCase):
    """The converter preserves strict decoding and its audit metadata contract."""

    def file(self, *, filename: str = "manual.txt", content_type: str = "text/plain") -> FileEntry:
        return FileEntry(
            id=uuid4(),
            filename=filename,
            content_type=content_type,
            size_bytes=10,
            storage_key=f"factory/{uuid4().hex}/content",
            sha256="a" * 64,
            created_at=datetime.now(timezone.utc),
        )

    def test_utf8_text_returns_conversion_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "content"
            path.write_bytes(b"\xef\xbb\xbfPump inspection")
            text, conversion = convert_plain_text(self.file(), path)

        self.assertEqual(text, "Pump inspection")
        self.assertEqual(conversion["encoding"], "utf-8-sig")
        self.assertEqual(len(conversion["sha256"]), 64)
        self.assertIn("+00:00", conversion["at"])

    def test_unsupported_and_unreadable_files_fail_without_replacement_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "content"
            path.write_bytes(b"\xff\x00\xfe")
            with self.assertRaisesRegex(JobFailed, re.escape(NO_CONVERTER)):
                convert_plain_text(self.file(filename="scan.pdf", content_type="application/pdf"), path)
            with self.assertRaisesRegex(JobFailed, re.escape(UNREADABLE_TEXT)):
                convert_plain_text(self.file(), path)


class DemoArtifactFileTest(unittest.TestCase):
    """The saved source snapshot has stable matching and record order."""

    def test_title_match_requires_demo_mode_and_pdf(self) -> None:
        artifact = read_demo_knowledge_extraction()
        with mock.patch.dict(os.environ, {DEMO_MODE_ENV: ""}):
            self.assertIsNone(
                load_demo_knowledge_extraction(artifact.title, PDF_CONTENT_TYPE)
            )
        with mock.patch.dict(os.environ, {DEMO_MODE_ENV: "true"}):
            self.assertIs(
                load_demo_knowledge_extraction("  REPORT2.PDF ", PDF_CONTENT_TYPE),
                artifact,
            )
            self.assertIsNone(
                load_demo_knowledge_extraction("other.pdf", PDF_CONTENT_TYPE)
            )
            self.assertIsNone(
                load_demo_knowledge_extraction(artifact.title, "text/plain")
            )

    def test_k110044_is_first_and_remaining_records_keep_source_order(self) -> None:
        artifact = read_demo_knowledge_extraction()
        self.assertEqual(len(artifact.text), 22_332)
        self.assertEqual(len(artifact.records), 19)
        self.assertEqual(
            [record.subject for record in artifact.records],
            [
                "Robot SCARA Omron i4L (trạm gấp đặt Line 2)",
                "Cell xếp pallet PACK2 - cụm đèn High Power",
                "Hệ thống nước làm mát VSC 0009",
                "Cụm turret VLA 0029",
                "Thanh dẫn shutter VEH 0019",
                "VSC 0009 (Máy rửa Rô-to No.4)",
                "VALA 0001 (Máy gia công MC20 số 1)",
                "Robot SCARA Omron i4L (cell lắp ráp C)",
                "Connector kiểm tra của các máy VIM",
                "Cell xếp pallet PACK2 - cụm đèn High Power",
                "VLA 0029 (Máy gia công bề mặt Rô-to A-10, A1)",
                "Giắc XSYSTEM tại trạm gấp đặt Line 2",
                "Robot SCARA Omron i4L (cell xếp pallet PACK2)",
                "Mạch liên động an toàn cell lắp ráp C",
                "VEH 0019 (Thổi khí No.1) - shutter DC3",
                "Robot SCARA Omron i4L - mạch cho phép High Power",
                "VIM 0200 (Máy kiểm tra tính năng No.6)",
                "Cell lắp ráp C - khóa liên động cửa",
                "MC20 số 1 - encoder trục X1",
            ],
        )


class DemoArtifactPipelineTest(unittest.IsolatedAsyncioTestCase):
    """The worker replays the configured PDF without conversion or model calls."""

    def setUp(self) -> None:
        self.artifact = read_demo_knowledge_extraction()
        self.kb_id = uuid4()
        self.source = SimpleNamespace(
            id=uuid4(),
            title=self.artifact.title,
            kind="document",
            extracted_text=None,
            metadata={},
            created_at=datetime.now(timezone.utc),
            file=SimpleNamespace(
                content_type=PDF_CONTENT_TYPE,
                filename=self.artifact.title,
                storage_key="hello/source/content",
                sha256="a" * 64,
            ),
        )
        self.spec = SimpleNamespace(
            name="hello_kb",
            retrieval=SimpleNamespace(value="knowledge"),
            embedding_context_keys=(),
            chunking=None,
            conflict_factory=mock.Mock(),
        )

    async def test_convert_uses_saved_text_and_configured_wait(self) -> None:
        """A matching demo PDF stores the saved text without reading its bytes."""

        async def update(_kb_id, _source_id, **values):
            self.source.extracted_text = values.get(
                "extracted_text", self.source.extracted_text
            )
            self.source.metadata.update(values.get("metadata", {}))
            return self.source

        sources = SimpleNamespace(update=AsyncMock(side_effect=update))
        uploads = SimpleNamespace(content_path=mock.Mock())
        pipeline = ApplicationSourcePipeline(None, sources, None, uploads, None)
        pipeline._source = AsyncMock(return_value=("hello_kb", self.spec, self.source))
        pipeline._sign = AsyncMock()
        job = SimpleNamespace(knowledge_base_id=self.kb_id, source_id=self.source.id)

        with (
            mock.patch(
                "backend.src.services.application_source_pipeline.load_demo_knowledge_extraction",
                return_value=self.artifact,
            ),
            mock.patch(
                "backend.src.services.application_source_pipeline.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            result = await pipeline.run_convert(job)

        sleep.assert_awaited_once_with(self.artifact.timing.conversion_seconds)
        uploads.content_path.assert_not_called()
        self.assertEqual(self.source.extracted_text, self.artifact.text)
        self.assertEqual(result["converter"], "demo_artifact")
        self.assertTrue(self.source.metadata["conversion"]["details"]["demo"])

    async def test_extract_saves_drafts_in_artifact_order_without_models(self) -> None:
        """Saved records use fresh IDs, K110044 first, and no model clients."""

        self.source.extracted_text = self.artifact.text
        sources = SimpleNamespace(update=AsyncMock(return_value=self.source))
        chunks = SimpleNamespace(
            has_results=AsyncMock(return_value=False),
            add_records_and_updates=AsyncMock(),
        )
        ingestion_factory = mock.Mock()
        embeddings_factory = mock.Mock()
        pipeline = ApplicationSourcePipeline(
            None,
            sources,
            chunks,
            None,
            object(),
            ingestion_factory=ingestion_factory,
            embeddings_factory=embeddings_factory,
        )
        pipeline._source = AsyncMock(return_value=("hello_kb", self.spec, self.source))
        pipeline._sign = AsyncMock()
        pipeline._overlaps = AsyncMock(return_value=[])
        run = SimpleNamespace(id=uuid4())
        runs = SimpleNamespace(
            find_active_original=AsyncMock(return_value=None),
            find_active=AsyncMock(return_value=None),
            embedding_models=AsyncMock(return_value=[]),
            start=AsyncMock(return_value=run),
            finish=AsyncMock(),
        )
        job = SimpleNamespace(
            id=uuid4(),
            knowledge_base_id=self.kb_id,
            source_id=self.source.id,
            mode="knowledge",
            options={},
            attempts=1,
        )

        with (
            mock.patch.dict(
                os.environ, {"EMBEDDING_DIM": "3", "EMBEDDING_MODEL": ""}
            ),
            mock.patch(
                "backend.src.services.application_source_pipeline.load_demo_knowledge_extraction",
                return_value=self.artifact,
            ),
            mock.patch(
                "backend.src.services.application_source_pipeline.ExtractionRunRepository",
                return_value=runs,
            ),
            mock.patch(
                "backend.src.services.application_source_pipeline.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            prepared = await pipeline.run_extract(job)
            self.assertIsInstance(prepared, PreparedJobResult)
            await prepared.persist(None)

        sleep.assert_awaited_once_with(self.artifact.timing.extraction_seconds)
        ingestion_factory.assert_not_called()
        embeddings_factory.assert_not_called()
        call = chunks.add_records_and_updates.await_args
        records = call.args[2]
        self.assertEqual(len(records), len(self.artifact.records))
        self.assertEqual(
            records[0].subject, "Robot SCARA Omron i4L (trạm gấp đặt Line 2)"
        )
        self.assertTrue(all(record.evidence_id == self.source.id for record in records))
        self.assertTrue(all(record.status.value == "draft" for record in records))
        self.assertTrue(all(record.embedding is None for record in records))
        self.assertEqual([record.id for record in records], sorted(record.id for record in records))
        self.assertEqual(prepared.result["stored"], len(records))

    async def test_conflict_stage_is_skipped_for_the_scripted_source(self) -> None:
        """The demo path does not invoke a conflict judge after replay."""

        pipeline = ApplicationSourcePipeline(None, None, None, None, None)
        pipeline._source = AsyncMock(return_value=("hello_kb", self.spec, self.source))
        job = SimpleNamespace(knowledge_base_id=self.kb_id, source_id=self.source.id)

        with mock.patch(
            "backend.src.services.application_source_pipeline.load_demo_knowledge_extraction",
            return_value=self.artifact,
        ):
            result = await pipeline.run_conflict(job)

        self.assertEqual(result, {"skipped": "scripted demo extraction"})
        self.spec.conflict_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()

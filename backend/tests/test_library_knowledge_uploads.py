"""Library-backed KB attachment, original downloads and worker conversion."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.src.services.application_source_pipeline import ApplicationSourcePipeline
from backend.src.services.file_library import FileLibraryService
from backend.src.storage.document_uploads import DocumentUploadStore
from backend.src.storage.file_library import FileBlobStore
from backend.tests.test_document_uploads import FakeAuth, FakeCatalog
from backend.tests.test_file_library import MemoryRecordStore
from backend.tests.test_file_types import PDF


class LibraryAttachmentTests(unittest.TestCase):
    """Exercise real byte storage with controlled repositories and job queueing."""

    def setUp(self) -> None:
        """Build an authenticated API with separate temporary library and legacy roots."""

        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.records = MemoryRecordStore()
        self.blobs = FileBlobStore(self.root / "files")
        self.library = FileLibraryService(self.records, self.blobs)
        self.store = DocumentUploadStore(self.root / "uploads", library_root=self.blobs.root)
        self.sources = SimpleNamespace(create_from_file=AsyncMock(), get=AsyncMock())
        self.catalog = FakeCatalog()
        self.manager = SimpleNamespace(
            extract=AsyncMock(return_value={"job": {"id": "job1", "stage": "convert", "status": "queued"}}),
            aclose=AsyncMock(), sources=self.sources,
            spec=lambda kb: (self.catalog.id, self.catalog.spec),
        )
        self.client = self.enterContext(TestClient(create_app(
            knowledge_agents={}, kb_catalog=self.catalog, auth_service=FakeAuth(),
            sources=self.sources, document_uploads=self.store,
            knowledge_management=self.manager, file_library=self.library,
        )))
        self.headers = {"Authorization": "Bearer access"}

    def upload(self) -> tuple[UUID, SimpleNamespace]:
        """Upload once through the real library service and prepare its KB source."""

        response = self.client.post("/v1/files", headers=self.headers,
                                    files={"file": ("manual.pdf", PDF, "application/pdf")})
        self.assertEqual(response.status_code, 201, response.text)
        file_id = UUID(response.json()["id"])
        source = SimpleNamespace(id=uuid4(), file=self.records.rows[file_id])
        self.sources.create_from_file.return_value = source, False
        self.sources.get.return_value = source
        return file_id, source

    def attach(self, file_id: UUID, mode: str | None = None):
        """Attach the library ID using the public JSON contract."""

        return self.client.post("/v1/documents/factory/files", headers=self.headers,
                                json={"file_id": str(file_id), "extract_mode": mode})

    def test_store_only_reuses_bytes_and_original_download_matches_library(self) -> None:
        """Attachment and both readers resolve the same bytes without another copy."""

        file_id, source = self.upload()
        response = self.attach(file_id)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["id"], source.id.hex)
        self.sources.create_from_file.assert_awaited_once_with(
            self.catalog.id, file_id=file_id, submitted_by=FakeAuth.account.id,
        )
        self.manager.extract.assert_not_awaited()
        self.assertEqual(len(list(self.blobs.root.iterdir())), 1)
        self.assertFalse(self.store.root.exists())
        for url in [f"/v1/files/{file_id}/content",
                    f"/v1/knowledge-management/factory/sources/upload/{source.id}/original"]:
            downloaded = self.client.get(url, headers=self.headers)
            self.assertEqual(downloaded.status_code, 200, downloaded.text)
            self.assertEqual(downloaded.content, PDF)
        listed = self.client.get("/v1/files", headers=self.headers).json()["files"]
        self.assertEqual(listed[0]["id"], str(file_id))

    def test_auto_queues_existing_pipeline_and_duplicate_does_not_queue_again(self) -> None:
        """Both extraction modes keep the existing queue contract and duplicate blocking."""

        for mode in ["knowledge", "documents"]:
            file_id, source = self.upload()
            response = self.attach(file_id, mode)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["job"]["stage"], "convert")
            self.manager.extract.assert_awaited_with(
                "factory", "upload", source.id.hex, mode, overlap_in_worker=True,
            )
            self.sources.create_from_file.return_value = source, True
            calls = self.manager.extract.await_count
            duplicate = self.attach(file_id, mode)
            self.assertEqual(duplicate.status_code, 200)
            self.assertTrue(duplicate.json()["duplicate"])
            self.assertEqual(self.manager.extract.await_count, calls)

    def test_queue_failure_keeps_saved_source_and_bytes(self) -> None:
        """A queue outage reports the retryable stored source without removing bytes."""

        file_id, source = self.upload()
        self.manager.extract.side_effect = HTTPException(503, "Queue unavailable")
        response = self.attach(file_id, "knowledge")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["job_error"], "Queue unavailable")
        self.assertEqual(self.blobs.path(source.file.storage_key).read_bytes(), PDF)

    def test_missing_foreign_and_deleted_files_return_same_404(self) -> None:
        """Attaching cannot bypass personal-library access checks."""

        from dataclasses import replace

        file_id, source = self.upload()
        unknown = self.attach(uuid4())
        self.records.rows[file_id] = replace(source.file, uploaded_by=uuid4())
        foreign = self.attach(file_id)
        self.records.rows[file_id] = source.file
        self.client.delete(f"/v1/files/{file_id}", headers=self.headers)
        deleted = self.attach(file_id)
        for response in [unknown, foreign, deleted]:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"detail": "File not found"})
        self.sources.create_from_file.assert_not_awaited()


class LibraryConversionTests(unittest.IsolatedAsyncioTestCase):
    """The worker resolves library keys while legacy source keys remain readable."""

    async def test_pdf_worker_reads_library_bytes_and_preserves_located_conversion(self) -> None:
        """Run real PDF conversion through the serving worker without an LLM or database."""

        import pymupdf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DocumentUploadStore(root / "uploads", library_root=root / "files")
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_textbox((72, 72, 500, 700), "Pump inspection: check seal. " * 30)
                content = document.tobytes()
            key = store.library.publish(store.library.save_temp(io.BytesIO(content), max_bytes=100_000))
            source = SimpleNamespace(
                id=uuid4(), title="manual.pdf", extracted_text=None, metadata={},
                file=SimpleNamespace(storage_key=key, filename="manual.pdf", content_type="application/pdf"),
            )

            async def update(*args, **values):
                """Retain converted text and evidence locations for assertions."""

                source.extracted_text = values.get("extracted_text", source.extracted_text)
                source.metadata.update(values.get("metadata", {}))
                return source

            sources = SimpleNamespace(update=AsyncMock(side_effect=update))
            pipeline = ApplicationSourcePipeline(None, sources, None, store, None)
            pipeline._source = AsyncMock(return_value=("factory", None, source))
            pipeline._sign = AsyncMock()
            await pipeline.run_convert(SimpleNamespace(knowledge_base_id=uuid4(), source_id=source.id))
            self.assertIn("Pump inspection", source.extracted_text)
            self.assertTrue(source.metadata["conversion"]["segments"])
            self.assertEqual(store.content_path(key).read_bytes(), content)
            legacy = store.save("factory", io.BytesIO(b"legacy"), filename="notes.txt", content_type="text/plain")
            self.assertEqual(store.content_path(legacy.storage_key).read_bytes(), b"legacy")
            for invalid in ["../secret", "file/../../secret", "/tmp/secret"]:
                with self.subTest(key=invalid), self.assertRaises(ValueError):
                    store.content_path(invalid)

"""Source lifecycle, queued processing and shared human review, without live models or databases."""

import asyncio
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.api.voice_capture import VoiceCaptureService
from backend.knowledge_bases import get_kb
from backend.src.agents.rag import ChunkHit
from backend.src.services.knowledge_extraction import KnowledgeExtractionResult, KnowledgeItem, KnowledgeType
from backend.src.services.knowledge_ingestion import KnowledgeIngestionService
from backend.src.services.job_worker import StageWorker
from backend.src.services.knowledge_management import KnowledgeManagementService
from backend.src.services.source_pipeline import UNREADABLE_TEXT
from backend.src.storage.document_uploads import DocumentUploadStore
from backend.src.storage.job_queue import STAGES, MemoryJobQueue
from backend.tests.test_knowledge_ingestion_service import FakeExtractor
from backend.tests.test_voice_capture import VoiceStore


class SourceStore(VoiceStore):
    async def list_conversations(self, *, offset=0, limit=100, **options):
        rows = await super().list_conversations(limit=limit, **options)
        for row in rows:
            row["knowledge_counts"] = dict(Counter(r.status.value for r in await self.knowledge_for_evidence(row["id"])))
        return rows[offset:offset + limit]

    async def mark_conversation(self, source_id, status, *, error=None):
        self.conversations[source_id] = self.conversations[source_id].model_copy(update={"status": status, "error": error})

    async def embed_knowledge(self, records):
        for record in records:
            record.embedding = [0.0]

    async def add_knowledge(self, records, **kwargs):
        self.records.update({record.id: record for record in records})
        return [record.id for record in records]


class ChunkStore:
    def __init__(self):
        self.chunks = {}

    async def ensure_config(self, config):
        self.config = config

    async def add_chunks(self, chunks):
        for chunk in chunks:
            self.chunks[chunk.id] = ChunkHit(chunk_id=chunk.id, doc_id=chunk.doc_id, chunk_index=chunk.index,
                                           title=chunk.title, text=chunk.text, score=0, metadata=dict(chunk.metadata))
        return len(chunks)

    async def get_document(self, doc_id):
        return [chunk for chunk in self.chunks.values() if chunk.doc_id == doc_id]

    async def document_summaries(self, **kwargs):
        return []

    async def review_chunk(self, doc_id, chunk_id, status, *, event):
        chunk = self.chunks.get(chunk_id)
        if not chunk or chunk.doc_id != doc_id:
            return False
        chunk.metadata["review_status"] = status
        chunk.metadata.setdefault("review_history", []).append(dict(event))
        return True

    async def aclose(self):
        pass


class KnowledgeManagementTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"EMBEDDING_DIM": "16"}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.uploads = DocumentUploadStore(Path(self.tmp.name) / "uploads")
        self.store = SourceStore()
        self.chunks = ChunkStore()
        self.extractor = FakeExtractor(KnowledgeExtractionResult(knowledge_items=[KnowledgeItem(
            subject="Trục Z", claim="Chưa xác nhận nguyên nhân.", type=KnowledgeType.FACT,
            context={"quotes": [{"quote": "Chưa xác nhận nguyên nhân."}]},
        )]))
        specs = {name: get_kb(name) for name in ("general", "maintenance")}

        async def open_store(spec):
            return self.store

        async def open_chunks(spec):
            return self.chunks

        self.capture = VoiceCaptureService(specs, audio_dir=Path(self.tmp.name) / "audio", open_store=open_store)
        self.queue = MemoryJobQueue(retry_delay_s=0)
        self.manager = KnowledgeManagementService(
            specs, self.uploads, self.capture, queue=self.queue, open_store=open_store, open_document_store=open_chunks,
            ingestion_factory=lambda spec, store: (KnowledgeIngestionService(self.extractor, store), {"version": "test"}),
        )
        # The API only queues; this worker plays both worker processes.
        self.worker = StageWorker(self.queue, self.manager, STAGES, name="test-worker")
        self.client = self.enterContext(TestClient(create_app(
            knowledge_agents={name: object() for name in specs}, voice_capture=self.capture,
            document_uploads=self.uploads, knowledge_management=self.manager,
        )))

    def upload(self, name="notes.txt", data="Chưa xác nhận nguyên nhân.\r\n".encode(), mime="text/plain", kb="general"):
        response = self.client.post(f"/v1/documents/{kb}/uploads", files={"file": (name, data, mime)})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("job", response.json())  # stored as raw only
        return response.json()["id"]

    def drain(self):
        return self.client.portal.call(self.worker.drain)

    def path(self, source_id, *, kb="general", kind="upload"):
        return f"/v1/knowledge-management/{kb}/sources/{kind}/{source_id}"

    def wait(self, path, status="done"):
        self.drain()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(detail["processing"].get("status"), status, detail)
        return detail

    def test_txt_extracts_directly_from_raw_without_manual_convert_and_reviews_chunks(self):
        source_id = self.upload()
        path = self.path(source_id)
        raw = self.client.get(path).json()
        self.assertEqual(raw["stage"], "raw")
        self.assertTrue(raw["can_extract_raw"])
        response = self.client.post(path + "/extract", json={"mode": "documents"})
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()["job"]
        self.assertEqual((job["stage"], job["status"], job["next_stages"]), ("convert", "queued", ["extract"]))
        queued = self.client.get(path).json()
        self.assertEqual((queued["stage"], queued["processing"]["status"]), ("raw", "queued"))
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "documents"}).status_code, 409)
        self.assertEqual(self.client.post(path + "/convert").status_code, 409)
        self.assertEqual(self.drain(), 2)  # convert, then the chained extract
        detail = self.wait(path)
        self.assertEqual(detail["processing"]["job"]["stage"], "extract")
        self.assertEqual([job.stage for job in sorted(self.queue.jobs.values(), key=lambda j: j.created_at)],
                         ["convert", "extract"])
        self.assertEqual((detail["stage"], detail["review"], detail["mode"]), ("extracted", "pending", "documents"))
        self.assertEqual(self.extractor.transcripts, [])
        self.assertEqual(detail["text"], "Chưa xác nhận nguyên nhân.\r\n")
        self.assertEqual((self.uploads.object_dir("general", source_id) / "content").read_bytes(), detail["text"].encode())
        chunk = detail["chunks"][0]
        self.assertEqual(chunk["status"], "draft")
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "knowledge"}).status_code, 409)
        for decision, expected in [("reject", "deprecated"), ("reset", "draft"), ("approve", "approved")]:
            response = self.client.post(path + "/review-chunk", json={"chunk_id": chunk["id"], "decision": decision, "note": "Đã đối chiếu"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["chunks"][0]["status"], expected)
        self.assertEqual(len(response.json()["chunks"][0]["history"]), 3)
        listing = self.client.get("/v1/knowledge-management/general/sources").json()
        self.assertEqual(len(listing["sources"]), 1)  # evidence row is not a second source
        self.assertEqual(listing["sources"][0]["review"], "reviewed")

    def test_knowledge_extraction_is_draft_and_shared_review_supports_document_kb(self):
        source_id = self.upload(kb="maintenance")
        path = self.path(source_id, kb="maintenance")
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "knowledge"}).status_code, 202)
        detail = self.wait(path)
        self.assertEqual(detail["records"][0]["status"], "draft")
        record = detail["records"][0]
        response = self.client.post(f"/v1/knowledge-management/maintenance/knowledge/{record['id']}/review", json={"decision": "approve"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "approved")
        self.assertEqual(response.json()["history"][0]["actor"], "knowledge management UI (unauthenticated)")
        self.assertEqual(len(self.store.conversations), 1)
        self.assertEqual(self.extractor.transcripts, ["Chưa xác nhận nguyên nhân.\r\n"])

    def test_media_stays_raw_and_other_text_formats_offer_conversion(self):
        pdf = self.upload("manual.pdf", b"%PDF-1.4", "application/pdf")
        path = self.path(pdf)
        detail = self.client.get(path).json()
        self.assertEqual(detail["stage"], "raw")
        self.assertFalse(detail["can_convert"])
        self.assertFalse(detail["can_extract_raw"])
        self.assertIn("OCR", detail["conversion_note"])
        self.assertEqual(self.client.post(path + "/convert").status_code, 422)
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "knowledge"}).status_code, 409)
        self.assertEqual(self.store.conversations, {})
        self.assertEqual(self.queue.jobs, {})
        md = self.upload("notes.md")
        md_path = self.path(md)
        self.assertTrue(self.client.get(md_path).json()["can_extract_raw"])
        converted = self.client.post(md_path + "/convert")
        self.assertEqual(converted.status_code, 202, converted.text)
        self.assertEqual((converted.json()["job"]["stage"], converted.json()["job"]["next_stages"]), ("convert", []))
        self.assertEqual(self.client.get(md_path).json()["stage"], "raw")
        self.drain()
        detail = self.client.get(md_path).json()
        self.assertEqual((detail["stage"], detail["processing"]["job"]["status"]), ("converted", "done"))
        self.assertEqual(self.client.post(md_path + "/convert").status_code, 409)
        self.assertEqual(self.store.conversations, {})  # convert alone never extracts

    def test_failed_extraction_is_retryable_without_duplicating_evidence(self):
        source_id = self.upload()
        path = self.path(source_id)
        self.extractor.result = RuntimeError("model unavailable")
        self.client.post(path + "/extract", json={"mode": "knowledge"})
        failed = self.wait(path, "failed")
        self.assertEqual(failed["stage"], "converted")
        # A service error is retried with backoff, then reported.
        self.assertEqual(failed["processing"]["job"]["attempts"], 3)
        self.assertIn("RuntimeError", failed["processing"]["error"])
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "documents"}).status_code, 202)
        detail = self.wait(path)
        self.assertEqual(len(self.store.conversations), 1)
        self.assertEqual(detail["review"], "pending")

    def test_invalid_text_remains_raw_and_original_can_be_downloaded(self):
        source_id = self.upload("binary.TXT", b"\xff\x00broken")
        path = self.path(source_id)
        self.assertTrue(self.client.get(path).json()["can_extract_raw"])
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "documents"}).status_code, 202)
        failed = self.wait(path, "failed")
        self.assertEqual(failed["stage"], "raw")
        # Unreadable text fails at once, and the chained extract is never queued.
        self.assertEqual((failed["processing"]["error"], failed["processing"]["job"]["attempts"]), (UNREADABLE_TEXT, 1))
        self.assertEqual([job.stage for job in self.queue.jobs.values()], ["convert"])
        self.assertEqual(self.client.get(path + "/original").content, b"\xff\x00broken")
        self.assertEqual(self.store.conversations, {})
        self.assertEqual(self.client.get(self.path(source_id, kb="missing")).status_code, 404)

    def test_transcripts_use_same_inventory_modes_and_review(self):
        row = self.client.portal.call(lambda: self.store.add_conversation(
            "[T01] Speaker A: Chưa xác nhận nguyên nhân.", metadata={
                "source_kind": "voice_recording", "name": "Ca đêm", "ingestion": {"status": "off"},
                "speakers": {"A": {"name": None}}, "cleaning": {"status": "off"},
            }))
        path = self.path(str(row.id), kind="conversation")
        detail = self.client.get(path).json()
        self.assertEqual(detail["stage"], "converted")
        self.assertIsNotNone(detail["conversation"])
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "documents"}).status_code, 202)
        detail = self.wait(path)
        self.assertEqual(detail["review"], "pending")
        listing = self.client.get("/v1/knowledge-management/general/sources").json()
        self.assertEqual(listing["sources"][0]["counts"], {"draft": 1})

    def test_conversion_persists_when_storage_is_reopened(self):
        source_id = self.upload("notes.md", b"\xef\xbb\xbfA\r\nB\n")
        self.client.post(self.path(source_id) + "/convert")
        self.drain()
        reopened = DocumentUploadStore(self.uploads.root)
        self.assertEqual(reopened.converted_text("general", source_id), "A\r\nB\n")
        self.assertEqual(len(reopened.list("general")), 1)

    def test_auto_upload_runs_the_whole_pipeline_in_the_workers(self):
        response = self.client.post("/v1/documents/general/uploads",
                                    files={"file": ("auto.txt", "Chưa xác nhận nguyên nhân.".encode(), "text/plain")},
                                    data={"extract_mode": "documents"})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual((body["job"]["stage"], body["job"]["next_stages"], body["job_error"]),
                         ("convert", ["extract"], None))
        self.assertEqual(self.store.conversations, {})  # nothing runs in the API
        detail = self.wait(self.path(body["id"]))
        self.assertEqual((detail["stage"], detail["review"]), ("extracted", "pending"))
        self.assertEqual(detail["chunks"][0]["status"], "draft")

    def test_auto_upload_without_a_converter_is_kept_raw(self):
        response = self.client.post("/v1/documents/general/uploads",
                                    files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
                                    data={"extract_mode": "knowledge"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIsNone(response.json()["job"])
        self.assertIn("OCR", response.json()["job_error"])
        self.assertEqual(self.client.get(self.path(response.json()["id"])).json()["stage"], "raw")

    def test_a_worker_crash_leaves_an_extraction_that_is_recovered_without_duplicates(self):
        source_id = self.upload()
        path = self.path(source_id)
        self.client.post(path + "/extract", json={"mode": "documents"})
        self.drain()
        extract = next(job for job in self.queue.jobs.values() if job.stage == "extract")
        # As if the worker died after saving chunks but before completing the job.
        self.queue.jobs[extract.id] = extract.__class__(**{**extract.__dict__, "status": "running",
                                                           "locked_by": "dead", "heartbeat_at": extract.created_at})
        listing = self.client.get("/v1/knowledge-management/general/sources").json()
        self.assertEqual(listing["sources"][0]["processing"]["status"], "running")
        recovered = self.client.portal.call(self.queue.recover_stale, 0)
        self.assertEqual([job.status for job in recovered], ["queued"])
        detail = self.wait(path)
        self.assertEqual(len(detail["chunks"]), len(self.chunks.chunks))
        self.assertEqual(self.queue.jobs[extract.id].result["skipped"], "already_extracted")


if __name__ == "__main__":
    unittest.main()

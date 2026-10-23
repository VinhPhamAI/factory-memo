"""Source lifecycle and shared human review, without live models or databases."""

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
from backend.src.services.knowledge_management import KnowledgeManagementService
from backend.src.storage.document_uploads import DocumentUploadStore
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
        self.manager = KnowledgeManagementService(
            specs, self.uploads, self.capture, open_store=open_store, open_document_store=open_chunks,
            ingestion_factory=lambda spec, store: (KnowledgeIngestionService(self.extractor, store), {"version": "test"}),
        )
        self.client = self.enterContext(TestClient(create_app(
            knowledge_agents={name: object() for name in specs}, voice_capture=self.capture,
            document_uploads=self.uploads, knowledge_management=self.manager,
        )))

    def upload(self, name="notes.txt", data="Chưa xác nhận nguyên nhân.\r\n".encode(), mime="text/plain", kb="general"):
        response = self.client.post(f"/v1/documents/{kb}/uploads", files={"file": (name, data, mime)})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def path(self, source_id, *, kb="general", kind="upload"):
        return f"/v1/knowledge-management/{kb}/sources/{kind}/{source_id}"

    def wait(self, path, status="done"):
        for _ in range(50):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            detail = response.json()
            if detail["processing"].get("status") == status:
                return detail
            asyncio.run(asyncio.sleep(.01))
        self.fail(str(detail))

    def test_txt_extracts_directly_from_raw_without_manual_convert_and_reviews_chunks(self):
        source_id = self.upload()
        path = self.path(source_id)
        raw = self.client.get(path).json()
        self.assertEqual(raw["stage"], "raw")
        self.assertTrue(raw["can_extract_raw"])
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "documents"}).status_code, 202)
        detail = self.wait(path)
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
        md = self.upload("notes.md")
        md_path = self.path(md)
        self.assertEqual(self.client.post(md_path + "/extract", json={"mode": "documents"}).status_code, 409)
        converted = self.client.post(md_path + "/convert")
        self.assertEqual(converted.status_code, 200, converted.text)
        self.assertEqual(converted.json()["stage"], "converted")
        self.assertEqual(self.store.conversations, {})

    def test_failed_extraction_is_retryable_without_duplicating_evidence(self):
        source_id = self.upload()
        path = self.path(source_id)
        self.extractor.result = RuntimeError("model unavailable")
        self.client.post(path + "/extract", json={"mode": "knowledge"})
        failed = self.wait(path, "failed")
        self.assertEqual(failed["stage"], "converted")
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "documents"}).status_code, 202)
        detail = self.wait(path)
        self.assertEqual(len(self.store.conversations), 1)
        self.assertEqual(detail["review"], "pending")

    def test_invalid_text_remains_raw_and_original_can_be_downloaded(self):
        source_id = self.upload("binary.TXT", b"\xff\x00broken")
        path = self.path(source_id)
        self.assertTrue(self.client.get(path).json()["can_extract_raw"])
        self.assertEqual(self.client.post(path + "/extract", json={"mode": "documents"}).status_code, 422)
        self.assertEqual(self.client.get(path).json()["stage"], "raw")
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
        reopened = DocumentUploadStore(self.uploads.root)
        self.assertEqual(reopened.converted_text("general", source_id), "A\r\nB\n")
        self.assertEqual(len(reopened.list("general")), 1)


if __name__ == "__main__":
    unittest.main()

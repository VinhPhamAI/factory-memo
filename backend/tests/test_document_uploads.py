"""Offline upload contract and disk persistence checks; no DB or LLM calls."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.src.storage.document_uploads import (
    PROJECT_ROOT,
    DocumentUploadStore,
    uploads_dir_from_env,
)


class DocumentUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "uploads"
        self.store = DocumentUploadStore(self.root, max_bytes=32)
        # Any attempted agent/DB ingestion would fail: the only fake is an opaque object.
        self.client = self.enterContext(TestClient(create_app(
            knowledge_agents={"general": object(), "industrial_qa": object()},
            document_uploads=self.store,
        )))

    def upload(self, filename="manual.pdf", content=b"original bytes", kb="general", mime="application/pdf"):
        return self.client.post(f"/v1/documents/{kb}/uploads", files={"file": (filename, content, mime)})

    def test_multiple_document_formats_preserve_bytes_and_metadata(self):
        for name, mime in [
            ("manual.pdf", "application/pdf"),
            ("ảnh.png", "image/png"),
            ("notes.txt", "text/plain"),
            ("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
            ("table.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("other.custom", "application/octet-stream"),
        ]:
            with self.subTest(name=name):
                response = self.upload(name, mime=mime)
                self.assertEqual(response.status_code, 201, response.text)
                document = response.json()
                self.assertEqual(document["status"], "stored")
                self.assertNotIn("conversation_id", document)
                self.assertEqual(document["filename"], name)
                self.assertEqual(document["content_type"], mime)
                self.assertEqual(document["size_bytes"], 14)
                self.assertEqual(document["sha256"], hashlib.sha256(b"original bytes").hexdigest())
                content = self.root / document["storage_key"]
                self.assertEqual(content.read_bytes(), b"original bytes")
                self.assertEqual(json.loads((content.parent / "metadata.json").read_text()), document)

    def test_duplicate_names_do_not_overwrite_and_kbs_are_separate(self):
        a = self.upload(content=b"first").json()
        b = self.upload(content=b"second").json()
        c = self.upload(kb="industrial_qa").json()
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual((self.root / a["storage_key"]).read_bytes(), b"first")
        self.assertEqual((self.root / b["storage_key"]).read_bytes(), b"second")
        self.assertTrue(c["storage_key"].startswith("industrial_qa/"))

    def test_empty_oversize_and_missing_files_do_not_publish_objects(self):
        self.assertEqual(self.upload(content=b"").status_code, 422)
        self.assertEqual(self.upload(content=b"x" * 33).status_code, 413)
        self.assertEqual(self.client.post("/v1/documents/general/uploads").status_code, 422)
        self.assertEqual(list((self.root / "general").iterdir()), [])
        self.assertEqual(self.upload(content=b"x" * 32).status_code, 201)

    def test_unknown_kb_does_not_create_storage(self):
        self.assertEqual(self.upload(kb="missing").status_code, 404)
        self.assertFalse(self.root.exists())

    def test_untrusted_filename_is_metadata_only(self):
        response = self.upload(filename="../../outside.txt")
        self.assertEqual(response.status_code, 201)
        document = response.json()
        self.assertEqual(document["filename"], "../../outside.txt")
        self.assertEqual((self.root / document["storage_key"]).name, "content")
        self.assertFalse((Path(self.tmp.name) / "outside.txt").exists())
        with self.assertRaises(ValueError):
            self.store.save("../outside", io.BytesIO(b"file"), filename="a", content_type="")

    def test_copy_failure_removes_partial_data_and_returns_storage_error(self):
        class BrokenSource:
            calls = 0

            def read(self, size):
                self.calls += 1
                if self.calls == 1:
                    return b"partial"
                raise OSError("disk or source error")

        with self.assertRaises(OSError):
            self.store.save("general", BrokenSource(), filename="file.txt", content_type="")
        self.assertEqual(list((self.root / "general").iterdir()), [])
        with self.assertLogs("backend.api.document_uploads", level="ERROR"), patch.object(
            self.store, "save", side_effect=OSError("private/path")
        ):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private/path", response.text)

    def test_default_and_configured_paths(self):
        with patch.dict("os.environ", {"DOCUMENT_UPLOAD_DIR": ""}):
            self.assertEqual(uploads_dir_from_env(), PROJECT_ROOT / "data" / "uploads")
        with patch.dict("os.environ", {"DOCUMENT_UPLOAD_DIR": str(self.root)}):
            self.assertEqual(DocumentUploadStore().root, self.root.resolve())


if __name__ == "__main__":
    unittest.main()

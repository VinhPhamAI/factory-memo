"""Checks for the personal file library.

Offline tests cover disk storage. PostgreSQL tests need TEST_DATABASE_URL (see
backend/tests/test_database_migrations.py); each class migrates its own
disposable database and drops it afterwards.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from unittest import mock
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.app import create_app
from backend.src.services.auth import InvalidSession
from backend.src.services.file_library import FileLibraryService, InvalidFilename, validate_filename
from backend.src.storage.auth import Account
from backend.src.storage.app_database import create_session_factory
from backend.src.storage.file_library import (
    PROJECT_ROOT,
    EmptyFile,
    FileBlobStore,
    FileRecord,
    FileRecordStore,
    FileStorageUnavailable,
    FileTooLarge,
    file_library_dir_from_env,
)
from backend.tests.test_auth import (
    FRONTEND_ORIGIN,
    PASSWORD,
    SETTINGS,
    PostgresAuthTest,
)
from backend.tests.test_database_migrations import DATABASE_URL
from backend.tests.test_file_types import DOCX, JPEG, PDF, PNG, XLSX


class FileBlobStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "files"
        self.store = FileBlobStore(self.root)

    def entries(self) -> list[str]:
        return sorted(path.name for path in self.root.iterdir()) if self.root.exists() else []

    def test_save_publish_and_remove(self) -> None:
        upload = self.store.save_temp(io.BytesIO(b"original bytes"), max_bytes=32)
        self.assertEqual(upload.size_bytes, 14)
        self.assertEqual(upload.sha256, hashlib.sha256(b"original bytes").hexdigest())

        key = self.store.publish(upload)
        self.assertRegex(key, r"^[0-9a-f]{32}$")
        self.assertEqual(self.store.path(key).read_bytes(), b"original bytes")
        self.assertEqual(self.entries(), [key])

        self.store.remove(key)
        self.assertEqual(self.entries(), [])

    def test_identical_content_is_stored_separately(self) -> None:
        first = self.store.publish(self.store.save_temp(io.BytesIO(b"same"), max_bytes=32))
        second = self.store.publish(self.store.save_temp(io.BytesIO(b"same"), max_bytes=32))
        self.assertNotEqual(first, second)
        self.assertEqual(self.entries(), sorted([first, second]))

    def test_limit_is_inclusive_and_oversized_upload_leaves_nothing(self) -> None:
        self.store.discard(self.store.save_temp(io.BytesIO(b"x" * 32), max_bytes=32))
        with self.assertRaises(FileTooLarge):
            self.store.save_temp(io.BytesIO(b"x" * 33), max_bytes=32)
        self.assertEqual(self.entries(), [])

    def test_empty_upload_leaves_nothing(self) -> None:
        with self.assertRaises(EmptyFile):
            self.store.save_temp(io.BytesIO(b""), max_bytes=32)
        self.assertEqual(self.entries(), [])

    def test_read_error_leaves_nothing(self) -> None:
        class FailingStream(io.RawIOBase):
            def read(self, _size: int = -1) -> bytes:
                raise OSError("disconnected")

        with self.assertRaises(OSError):
            self.store.save_temp(FailingStream(), max_bytes=32)
        self.assertEqual(self.entries(), [])

    def test_discard_removes_unpublished_upload(self) -> None:
        upload = self.store.save_temp(io.BytesIO(b"bytes"), max_bytes=32)
        self.store.discard(upload)
        self.store.discard(upload)  # A second discard is harmless.
        self.assertEqual(self.entries(), [])

    def test_storage_keys_never_leave_the_root(self) -> None:
        for key in ["../secret", "a/b", "", "0" * 31, "Z" * 32]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.path(key)

    def test_root_comes_from_environment_or_project_data(self) -> None:
        with mock.patch.dict("os.environ", {"FILE_LIBRARY_DIR": ""}):
            self.assertEqual(file_library_dir_from_env(), PROJECT_ROOT / "data" / "files")
        with mock.patch.dict("os.environ", {"FILE_LIBRARY_DIR": self.tmp.name}):
            self.assertEqual(file_library_dir_from_env(), Path(self.tmp.name).resolve())


class ValidateFilenameTest(unittest.TestCase):
    def test_accepts_ordinary_names(self) -> None:
        for name in ["manual.pdf", "Hướng dẫn máy 01.docx", "x" * 251 + ".pdf"]:
            with self.subTest(name=name[:20]):
                validate_filename(name)

    def test_rejects_blank_long_and_control_characters(self) -> None:
        for name in ["", "   ", "x" * 252 + ".pdf", "bad\x01name.pdf", "line\nbreak.pdf", "nul\x00.pdf"]:
            with self.subTest(name=name[:20]), self.assertRaises(InvalidFilename):
                validate_filename(name)


ALICE = Account(id=uuid4(), username="alice", display_name="Alice", role="employee")
BOB = Account(id=uuid4(), username="bob", display_name="Bob", role="employee")
ALICE_HEADERS = {"Authorization": "Bearer alice-token"}
BOB_HEADERS = {"Authorization": "Bearer bob-token"}


class StubAuthService:
    """Accepts two fixed bearer tokens; no database."""

    async def authenticate_access(self, token: str) -> Account:
        accounts = {"alice-token": ALICE, "bob-token": BOB}
        if token not in accounts:
            raise InvalidSession(token)
        return accounts[token]


class MemoryRecordStore:
    """In-memory stand-in for FileRecordStore with the same filtering rules."""

    def __init__(self) -> None:
        self.rows: dict[UUID, FileRecord] = {}

    async def insert(self, **fields: Any) -> FileRecord:
        record = FileRecord(id=uuid4(), created_at=datetime.now(timezone.utc), deleted_at=None, **fields)
        self.rows[record.id] = record
        return record

    async def get(self, file_id: UUID) -> FileRecord | None:
        return self.rows.get(file_id)

    async def list_owned(self, user_id: UUID) -> list[FileRecord]:
        owned = [r for r in self.rows.values() if r.uploaded_by == user_id and r.deleted_at is None]
        return sorted(owned, key=lambda r: (r.created_at, r.id), reverse=True)

    async def soft_delete(self, file_id: UUID, user_id: UUID) -> bool:
        record = self.rows.get(file_id)
        if record is None or record.uploaded_by != user_id or record.deleted_at is not None:
            return False
        self.rows[file_id] = FileRecord(**{**record.__dict__, "deleted_at": datetime.now(timezone.utc)})
        return True


class OfflineLibrary(unittest.TestCase):
    """A file library over a temporary directory and in-memory records."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "files"
        self.records = MemoryRecordStore()
        self.library = FileLibraryService(self.records, FileBlobStore(self.root), max_bytes=1024)

    def stored(self) -> list[str]:
        return sorted(path.name for path in self.root.iterdir()) if self.root.exists() else []


class FileLibraryServiceTest(OfflineLibrary):
    def upload(self, filename: str = "manual.pdf", content: bytes = PDF) -> FileRecord:
        return asyncio.run(self.library.upload(ALICE, filename, io.BytesIO(content)))

    def test_rejected_type_leaves_nothing(self) -> None:
        with self.assertRaises(ValueError):
            self.upload("program.pdf", b"MZ executable")
        self.assertEqual(self.stored(), [])

    def test_failed_insert_removes_published_bytes(self) -> None:
        with mock.patch.object(self.records, "insert", side_effect=FileStorageUnavailable("down")):
            with self.assertRaises(FileStorageUnavailable):
                self.upload()
        self.assertEqual(self.stored(), [])

    def test_cancelled_insert_keeps_bytes_because_the_commit_may_have_succeeded(self) -> None:
        with mock.patch.object(self.records, "insert", side_effect=asyncio.CancelledError()):
            with self.assertRaises(asyncio.CancelledError):
                self.upload()
        self.assertEqual(len(self.stored()), 1)

    def test_can_read_only_own_files_that_are_not_deleted(self) -> None:
        record = self.upload()
        self.assertTrue(self.library.can_read(record, ALICE))
        self.assertFalse(self.library.can_read(record, BOB))
        asyncio.run(self.library.delete(ALICE, record.id))
        self.assertFalse(self.library.can_read(self.records.rows[record.id], ALICE))


class OfflineFileApiTest(OfflineLibrary):
    def setUp(self) -> None:
        super().setUp()
        app = create_app(knowledge_agents={}, auth_service=StubAuthService(), file_library=self.library)
        self.client = self.enterContext(TestClient(app, base_url="https://testserver"))

    def test_body_without_content_length_is_refused_before_reading(self) -> None:
        def chunks():
            yield b"--x\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.pdf\"\r\n\r\n"
            yield PDF + b"\r\n--x--\r\n"

        response = self.client.post(
            "/v1/files", content=chunks(),
            headers={**ALICE_HEADERS, "Content-Type": "multipart/form-data; boundary=x"},
        )
        self.assertEqual(response.status_code, 411)
        self.assertEqual(self.stored(), [])

    def test_transfer_encoding_is_refused_even_with_a_content_length(self) -> None:
        # Transfer-Encoding wins over Content-Length, so the body could exceed it.
        response = self.client.post(
            "/v1/files", files={"file": ("a.pdf", PDF, "application/pdf")},
            headers={**ALICE_HEADERS, "Transfer-Encoding": "chunked"},
        )
        self.assertEqual(response.status_code, 411)
        self.assertEqual(self.stored(), [])

    def test_more_than_one_file_is_rejected(self) -> None:
        response = self.client.post(
            "/v1/files", headers=ALICE_HEADERS,
            files=[("file", ("a.pdf", PDF, "application/pdf")), ("file", ("b.pdf", PDF, "application/pdf"))],
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored(), [])

    def test_filename_with_header_characters_downloads_safely(self) -> None:
        name = 'a"b;c%d.pdf'
        created = self.client.post("/v1/files", headers=ALICE_HEADERS, files={"file": (name, PDF, "x/y")})
        self.assertEqual(created.status_code, 201, created.text)
        # The test client escapes '"' as %22 in multipart names; use the stored name.
        stored_name = created.json()["filename"]
        self.assertIn(";", stored_name)
        response = self.client.get(f"/v1/files/{created.json()['id']}/content", headers=ALICE_HEADERS)
        disposition = response.headers["content-disposition"]
        self.assertEqual(disposition, f"attachment; filename*=utf-8''{quote(stored_name)}")

    def test_bidirectional_control_in_filename_is_rejected(self) -> None:
        response = self.client.post(
            "/v1/files", headers=ALICE_HEADERS, files={"file": ("invoice\u202efdp.pdf", PDF, "x/y")},
        )
        self.assertEqual(response.status_code, 422)

    def test_missing_bytes_look_like_a_missing_file(self) -> None:
        created = self.client.post("/v1/files", headers=ALICE_HEADERS, files={"file": ("a.pdf", PDF, "x/y")})
        for key in self.stored():
            (self.root / key).unlink()
        response = self.client.get(f"/v1/files/{created.json()['id']}/content", headers=ALICE_HEADERS)
        self.assertEqual(response.status_code, 404)

    def test_preview_is_owner_only_and_renders_pdf_first_page(self) -> None:
        import pymupdf

        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((36, 72), "Preview")
            content = pdf.tobytes()
        self.library.max_bytes = 10_000
        created = self.client.post("/v1/files", headers=ALICE_HEADERS, files={"file": ("a.pdf", content, "application/pdf")})
        self.assertEqual(created.status_code, 201, created.text)
        path = f"/v1/files/{created.json()['id']}/preview"
        self.assertEqual(self.client.get(path, headers=BOB_HEADERS).status_code, 404)
        preview = self.client.get(path, headers=ALICE_HEADERS)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.headers["content-type"], "image/png")
        self.assertTrue(preview.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_preview_rejects_corrupt_content(self) -> None:
        created = self.client.post("/v1/files", headers=ALICE_HEADERS, files={"file": ("a.pdf", PDF, "application/pdf")})
        result = self.client.get(f"/v1/files/{created.json()['id']}/preview", headers=ALICE_HEADERS)
        self.assertEqual(result.status_code, 422)

    def test_png_preview(self) -> None:
        import pymupdf

        with pymupdf.open() as pdf:
            image = pdf.new_page(width=40, height=60).get_pixmap().tobytes("png")
        self.library.max_bytes = 10_000
        created = self.client.post("/v1/files", headers=ALICE_HEADERS, files={"file": ("image.png", image, "image/png")})
        self.assertEqual(created.status_code, 201, created.text)
        preview = self.client.get(f"/v1/files/{created.json()['id']}/preview", headers=ALICE_HEADERS)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.headers["content-type"], "image/png")

    def test_office_previews_show_content(self) -> None:
        from docx import Document
        from openpyxl import Workbook

        document = Document()
        document.add_paragraph("Machine manual")
        doc_bytes = io.BytesIO()
        document.save(doc_bytes)
        workbook = Workbook()
        workbook.active.append(["Pressure", 0])
        xls_bytes = io.BytesIO()
        workbook.save(xls_bytes)
        self.library.max_bytes = 100_000
        for name, content, expected in [
            ("manual.docx", doc_bytes.getvalue(), "Machine manual"),
            ("sheet.xlsx", xls_bytes.getvalue(), "Pressure\t0"),
        ]:
            with self.subTest(name=name):
                created = self.client.post("/v1/files", headers=ALICE_HEADERS, files={"file": (name, content, "application/octet-stream")})
                self.assertEqual(created.status_code, 201, created.text)
                preview = self.client.get(f"/v1/files/{created.json()['id']}/preview", headers=ALICE_HEADERS)
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertIn(expected, preview.text)


class UnconfiguredFileLibraryTest(unittest.TestCase):
    def test_signed_in_requests_report_unavailable_without_a_service(self) -> None:
        app = create_app(knowledge_agents={}, auth_service=StubAuthService())
        with TestClient(app, base_url="https://testserver") as client:
            response = client.get("/v1/files", headers=ALICE_HEADERS)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "The file library is not configured")


METADATA_FIELDS = {"id", "filename", "content_type", "size_bytes", "sha256", "created_at"}
MAX_TEST_BYTES = 1024


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class FileLibraryApiTest(PostgresAuthTest):
    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "files"
        auth, _auth_engine = self.new_service()
        # NullPool: connections never outlive the event loop that opened them.
        engine = create_async_engine(self.database.url, poolclass=NullPool)
        self.library = FileLibraryService(
            FileRecordStore(create_session_factory(engine)),
            FileBlobStore(self.root),
            max_bytes=MAX_TEST_BYTES,
        )
        app = create_app(
            knowledge_agents={}, auth_service=auth, auth_settings=SETTINGS, file_library=self.library,
        )
        self.client = self.enterContext(TestClient(app, base_url="https://testserver"))
        self.alice = self.new_user()
        self.bob = self.new_user()

    def new_user(self) -> dict[str, str]:
        username = f"user-{uuid4().hex[:8]}"
        headers = {"Origin": FRONTEND_ORIGIN}
        body = {"username": username, "display_name": "An Nguyen", "password": PASSWORD}
        self.assertEqual(self.client.post("/auth/signup", json=body, headers=headers).status_code, 201)
        login = self.client.post("/auth/login", json={"username": username, "password": PASSWORD},
                                 headers=headers)
        self.client.cookies.clear()
        return {"Authorization": f"Bearer {login.json()['access_token']}"}

    def upload(self, user: dict[str, str], filename: str = "manual.pdf", content: bytes = PDF,
               mime: str = "application/octet-stream") -> Any:
        return self.client.post("/v1/files", headers=user, files={"file": (filename, content, mime)})

    def uploaded(self, user: dict[str, str], filename: str = "manual.pdf", content: bytes = PDF) -> dict:
        response = self.upload(user, filename, content)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def stored(self) -> list[str]:
        return sorted(path.name for path in self.root.iterdir()) if self.root.exists() else []

    def test_upload_accepted_types_with_server_chosen_content_type(self) -> None:
        for filename, content, expected in [
            ("manual.pdf", PDF, "application/pdf"),
            ("photo.png", PNG, "image/png"),
            ("photo.JPG", JPEG, "image/jpeg"),
            ("report.docx", DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("sheet.xlsx", XLSX, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ]:
            with self.subTest(filename=filename):
                body = self.uploaded(self.alice, filename, content)
                self.assertEqual(set(body), METADATA_FIELDS)
                self.assertEqual(body["filename"], filename)
                self.assertEqual(body["content_type"], expected)
                self.assertEqual(body["size_bytes"], len(content))
                self.assertEqual(body["sha256"], hashlib.sha256(content).hexdigest())

    def test_list_shows_only_own_files_newest_first(self) -> None:
        first = self.uploaded(self.alice, "first.pdf")
        second = self.uploaded(self.alice, "second.png", PNG)
        self.uploaded(self.bob, "bob.pdf")

        response = self.client.get("/v1/files", headers=self.alice)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["files"]], [second["id"], first["id"]])
        self.assertEqual(self.client.get("/v1/files", headers=self.bob).json()["files"][0]["filename"],
                         "bob.pdf")

    def test_knowledge_uploads_stay_out_of_the_personal_library(self) -> None:
        """Keep KB evidence outside personal listing, downloads and deletion."""

        personal = self.uploaded(self.alice)
        owner = self.sql("SELECT uploaded_by FROM files WHERE id = :id",
                         id=UUID(personal["id"])).scalar_one()
        file_id = uuid4()
        storage_key = f"line_a/{uuid4().hex}/content"
        self.sql(
            "INSERT INTO files (id, filename, content_type, size_bytes, storage_key, "
            "sha256, uploaded_by) VALUES (:id, 'evidence.pdf', 'application/pdf', "
            ":size, :key, :sha, :owner)",
            id=file_id, size=len(PDF), key=storage_key,
            sha=hashlib.sha256(PDF).hexdigest(), owner=owner,
        )
        listing = self.client.get("/v1/files", headers=self.alice)
        self.assertEqual([item["id"] for item in listing.json()["files"]], [personal["id"]])
        for method, path in (
            ("GET", f"/v1/files/{file_id}"),
            ("GET", f"/v1/files/{file_id}/content"),
            ("DELETE", f"/v1/files/{file_id}"),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path, headers=self.alice).status_code, 404)
        self.assertIsNone(self.sql("SELECT deleted_at FROM files WHERE id = :id",
                                   id=file_id).scalar_one())
        download = self.client.get(f"/v1/files/{personal['id']}/content", headers=self.alice)
        self.assertEqual(download.content, PDF)

    def test_metadata_and_download_of_own_file(self) -> None:
        body = self.uploaded(self.alice, "Hướng dẫn máy.pdf")
        self.assertEqual(self.client.get(f"/v1/files/{body['id']}", headers=self.alice).json(), body)

        response = self.client.get(f"/v1/files/{body['id']}/content", headers=self.alice)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        disposition = response.headers["content-disposition"]
        self.assertTrue(disposition.startswith("attachment;"), disposition)
        self.assertIn(f"filename*=utf-8''{quote('Hướng dẫn máy.pdf')}", disposition)

    def test_rejected_uploads_leave_nothing_stored(self) -> None:
        cases = [
            ("program.pdf", b"MZ\x90\x00 executable", 415),
            ("notes.txt", b"plain text", 415),
            ("archive.docx", b"PK\x03\x04 not a zip", 415),
            ("empty.pdf", b"", 422),
            ("big.pdf", PDF + b"x" * MAX_TEST_BYTES, 413),
            ("x" * 252 + ".pdf", PDF, 422),
        ]
        for filename, content, status in cases:
            with self.subTest(filename=filename[:20]):
                self.assertEqual(self.upload(self.alice, filename, content).status_code, status)
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.client.get("/v1/files", headers=self.alice).json()["files"], [])

    def test_request_without_file_field_is_rejected(self) -> None:
        response = self.client.post("/v1/files", headers=self.alice, data={"other": "value"})
        self.assertEqual(response.status_code, 422)

    def test_body_far_over_the_limit_is_rejected(self) -> None:
        response = self.upload(self.alice, "huge.pdf", PDF + b"x" * (2 * 1024 * 1024))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.stored(), [])

    def test_other_users_files_look_missing(self) -> None:
        file_id = self.uploaded(self.alice)["id"]
        for method, path in [
            ("GET", f"/v1/files/{file_id}"),
            ("GET", f"/v1/files/{file_id}/content"),
            ("DELETE", f"/v1/files/{file_id}"),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path, headers=self.bob).status_code, 404)
        self.assertEqual(self.client.get(f"/v1/files/{file_id}", headers=self.alice).status_code, 200)

    def test_unknown_and_malformed_ids(self) -> None:
        self.assertEqual(self.client.get(f"/v1/files/{uuid4()}", headers=self.alice).status_code, 404)
        self.assertEqual(self.client.get("/v1/files/not-a-uuid", headers=self.alice).status_code, 422)

    def test_delete_hides_file_but_keeps_record_and_bytes(self) -> None:
        file_id = self.uploaded(self.alice)["id"]
        self.assertEqual(self.client.delete(f"/v1/files/{file_id}", headers=self.alice).status_code, 204)

        self.assertEqual(self.client.get("/v1/files", headers=self.alice).json()["files"], [])
        for method, path in [
            ("GET", f"/v1/files/{file_id}"),
            ("GET", f"/v1/files/{file_id}/content"),
            ("DELETE", f"/v1/files/{file_id}"),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path, headers=self.alice).status_code, 404)

        row = self.sql("SELECT storage_key, deleted_at FROM files WHERE id = :id", id=UUID(file_id)).one()
        self.assertIsNotNone(row.deleted_at)
        self.assertEqual(self.stored(), [row.storage_key])

    def test_failed_record_insert_removes_published_bytes(self) -> None:
        failure = FileStorageUnavailable("The application database is unavailable")
        with mock.patch.object(self.library.records, "insert", side_effect=failure):
            response = self.upload(self.alice)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("password", response.text)
        self.assertEqual(self.stored(), [])

    def test_every_route_requires_a_valid_access_token(self) -> None:
        file_id = self.uploaded(self.alice)["id"]
        for headers in [{}, {"Authorization": "Bearer not-a-token"}]:
            for method, path in [
                ("POST", "/v1/files"),
                ("GET", "/v1/files"),
                ("GET", f"/v1/files/{file_id}"),
                ("GET", f"/v1/files/{file_id}/content"),
                ("DELETE", f"/v1/files/{file_id}"),
            ]:
                with self.subTest(method=method, path=path, headers=bool(headers)):
                    self.assertEqual(self.client.request(method, path, headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()

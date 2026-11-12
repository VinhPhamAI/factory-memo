"""Regression checks for the shared KB, conversation and file-library startup."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app import create_app
from backend.api.knowledge_bases import KnowledgeBaseCatalog
from backend.src.storage.repositories.knowledge_bases import KnowledgeBaseRepository
from backend.src.storage.orm import User
from backend.tests.test_auth import FRONTEND_ORIGIN, PASSWORD, SETTINGS
from backend.tests.test_database_migrations import DATABASE_URL, MigratedDatabaseTest
from backend.tests.test_file_types import PDF
from backend.tests.test_file_library import ALICE_HEADERS, StubAuthService


class MissingDatabaseStartupTest(unittest.TestCase):
    """Keep independently configured routes available without a database URL."""

    def test_missing_database_keeps_health_and_returns_unavailable(self) -> None:
        """Start normally and report unavailable storage without failing the lifespan."""

        with (
            mock.patch('backend.app.load_dotenv'),
            mock.patch.dict('os.environ', {'APP_DATABASE_URL': ''}),
            mock.patch('backend.app.AgentSettings.from_env', return_value=None),
            TestClient(create_app(auth_service=StubAuthService())) as client,
        ):
            self.assertEqual(client.get('/healthz').status_code, 200)
            for path in ('/v1/files', '/v1/conversations', '/v1/knowledge-bases',
                         '/v1/knowledge-management/general/sources'):
                with self.subTest(path=path):
                    response = client.get(path, headers=ALICE_HEADERS)
                    self.assertEqual(response.status_code, 503, response.text)
            response = client.post('/v1/documents/general/uploads', headers=ALICE_HEADERS,
                                   files={'file': ('note.txt', b'original note', 'text/plain')})
            self.assertEqual(response.status_code, 503, response.text)

    def test_knowledge_catalog_recovers_after_database_becomes_ready(self) -> None:
        """Retry transient startup validation without restarting the API."""

        validation = mock.AsyncMock(
            side_effect=[OSError("database starting"), OSError("database starting"), None]
        )
        list_active = mock.AsyncMock(return_value=[])
        with (
            mock.patch('backend.app.load_dotenv'),
            mock.patch.dict('os.environ', {
                'APP_DATABASE_URL': 'postgresql+psycopg://test:test@database/test',
                'EMBEDDING_DIM': '3',
            }),
            mock.patch('backend.app.AgentSettings.from_env', return_value=None),
            mock.patch('backend.app.verify_embedding_dimension', validation),
            mock.patch.object(KnowledgeBaseRepository, 'list_active', list_active),
            mock.patch.object(KnowledgeBaseCatalog, 'start_listener'),
            TestClient(create_app(auth_service=StubAuthService())) as client,
        ):
            unavailable = client.get('/v1/knowledge-bases', headers=ALICE_HEADERS)
            self.assertEqual(unavailable.status_code, 503, unavailable.text)

            recovered = client.get('/v1/knowledge-bases', headers=ALICE_HEADERS)
            self.assertEqual(recovered.status_code, 200, recovered.text)
            self.assertEqual(recovered.json()['knowledge_bases'], [])

            still_ready = client.get('/v1/knowledge-bases', headers=ALICE_HEADERS)
            self.assertEqual(still_ready.status_code, 200, still_ready.text)

        self.assertEqual(validation.await_count, 3)
        self.assertEqual(list_active.await_count, 2)


@unittest.skipUnless(DATABASE_URL, 'TEST_DATABASE_URL is not set')
class SharedApplicationStartupTest(MigratedDatabaseTest):
    """Exercise both upload routes through the production service wiring."""

    def test_embedding_mismatch_does_not_disable_main_services(self) -> None:
        """Keep chat and file routes available while incompatible KB storage is disabled."""

        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch("backend.app.load_dotenv"),
            mock.patch("backend.app.AgentSettings.from_env", return_value=None),
            mock.patch.dict("os.environ", {
                "APP_DATABASE_URL": self.database.url,
                "EMBEDDING_DIM": "4",
                "FILE_LIBRARY_DIR": root,
            }),
            TestClient(create_app(auth_service=StubAuthService())) as client,
        ):
            for path in ("/healthz", "/v1/files", "/v1/conversations"):
                with self.subTest(path=path):
                    response = client.get(path, headers=ALICE_HEADERS)
                    self.assertEqual(response.status_code, 200, response.text)
            unavailable = client.get("/v1/knowledge-bases", headers=ALICE_HEADERS)
            self.assertEqual(unavailable.status_code, 503, unavailable.text)

    def test_knowledge_upload_and_personal_library_use_their_own_bytes(self) -> None:
        """Keep personal uploads downloadable and KB evidence independently readable."""

        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch('backend.app.load_dotenv'),
            mock.patch('backend.app.AgentSettings.from_env', return_value=None),
            mock.patch.dict('os.environ', {
                'APP_DATABASE_URL': self.database.url,
                'EMBEDDING_DIM': '3',
                'FILE_LIBRARY_DIR': str(Path(root) / 'files'),
                'DOCUMENT_UPLOAD_DIR': str(Path(root) / 'uploads'),
            }),
            TestClient(create_app(auth_settings=SETTINGS), base_url='https://testserver') as client,
        ):
            credentials = {'username': 'shared-startup', 'password': PASSWORD}
            signup = client.post('/auth/signup', json={**credentials, 'display_name': 'Expert'},
                                 headers={'Origin': FRONTEND_ORIGIN})
            self.assertEqual(signup.status_code, 201, signup.text)
            with Session(self.engine) as session, session.begin():
                session.execute(update(User).where(User.username == credentials['username'])
                                .values(role='expert'))
            login = client.post('/auth/login', json=credentials, headers={'Origin': FRONTEND_ORIGIN})
            self.assertEqual(login.status_code, 200, login.text)
            headers = {'Authorization': f"Bearer {login.json()['access_token']}"}
            created = client.post('/v1/knowledge-bases', headers=headers,
                                  json={'name': 'line_a', 'title': 'Line A'})
            self.assertEqual(created.status_code, 201, created.text)
            personal = client.post('/v1/files', headers=headers,
                                   files={'file': ('manual.pdf', PDF, 'application/pdf')})
            self.assertEqual(personal.status_code, 201, personal.text)
            original = b'Original KB evidence'
            source = client.post('/v1/documents/line_a/uploads', headers=headers,
                                 files={'file': ('note.txt', original, 'text/plain')})
            self.assertEqual(source.status_code, 201, source.text)
            listing = client.get('/v1/files', headers=headers)
            self.assertEqual([item['id'] for item in listing.json()['files']], [personal.json()['id']])
            personal_download = client.get(f"/v1/files/{personal.json()['id']}/content", headers=headers)
            self.assertEqual(personal_download.content, PDF)
            linked = client.post('/v1/documents/line_a/files', headers=headers, json={
                'file_id': personal.json()['id'], 'extract_mode': 'documents',
            })
            self.assertEqual(linked.status_code, 201, linked.text)
            self.assertEqual(linked.json()['job']['stage'], 'convert')
            self.assertEqual(linked.json()['job']['next_stages'], ['extract'])
            library_evidence_path = (
                f"/v1/knowledge-management/line_a/sources/upload/{linked.json()['id']}/original"
            )
            self.assertEqual(client.get(library_evidence_path, headers=headers).content, PDF)
            evidence_path = f"/v1/knowledge-management/line_a/sources/upload/{source.json()['id']}/original"
            evidence = client.get(evidence_path, headers=headers)
            self.assertEqual(evidence.status_code, 200, evidence.text)
            self.assertEqual(evidence.content, original)
            deleted = client.delete(f"/v1/files/{personal.json()['id']}", headers=headers)
            self.assertEqual(deleted.status_code, 204, deleted.text)
            self.assertEqual(client.get(evidence_path, headers=headers).content, original)
            self.assertEqual(client.get(library_evidence_path, headers=headers).content, PDF)
            detail = client.get(library_evidence_path.removesuffix('/original'), headers=headers)
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()['processing']['job']['status'], 'queued')

"""Regression checks for the shared KB, conversation and file-library startup."""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import create_app
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

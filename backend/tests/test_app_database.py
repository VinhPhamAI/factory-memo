"""Checks that the serving process connects to the application database only."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from backend.src.storage.app_database import app_database_url_from_env, create_app_engine


APP_URL = "postgresql+psycopg://runtime:secret@localhost:5434/voice_agent_app"
MIGRATION_URL = "postgresql+psycopg://owner:secret@localhost:5434/voice_agent_app"


class AppDatabaseUrlTest(unittest.TestCase):
    def test_runtime_uses_app_database_url_not_the_migration_url(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"APP_DATABASE_URL": APP_URL, "MIGRATION_DATABASE_URL": MIGRATION_URL},
        ):
            self.assertEqual(app_database_url_from_env(), APP_URL)

    def test_missing_app_database_url_is_not_replaced_by_other_urls(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"APP_DATABASE_URL": "", "MIGRATION_DATABASE_URL": MIGRATION_URL,
             "DATABASE_URL": MIGRATION_URL},
        ):
            self.assertIsNone(app_database_url_from_env())

    def test_engine_targets_the_given_url_without_connecting(self) -> None:
        engine = create_app_engine(APP_URL)
        try:
            self.assertEqual(engine.url.username, "runtime")
            self.assertEqual(engine.url.database, "voice_agent_app")
            # Error messages must not carry values such as password hashes.
            self.assertTrue(engine.sync_engine.hide_parameters)
        finally:
            engine.sync_engine.dispose()


if __name__ == "__main__":
    unittest.main()

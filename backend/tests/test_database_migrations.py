"""Checks for the backend application models and their Alembic migrations.

Unit tests run offline. Integration tests need PostgreSQL + pgvector:
    docker compose -f deployments/docker-compose.yml up -d postgres
    TEST_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent \
        uv run python -m unittest backend.tests.test_database_migrations -v

Each integration test class migrates its own disposable database and drops it
afterwards; the database in TEST_DATABASE_URL is only used to create and drop it.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, create_engine, inspect, select, text, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.src.storage.orm import (
    AuthSession,
    Base,
    Conversation,
    ExtractionMode,
    ExtractionRun,
    File,
    IngestionJob,
    Inquiry,
    InquiryEvent,
    InquiryEventKind,
    InquiryFile,
    JobStage,
    JobStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeChunkSource,
    KnowledgeConflict,
    KnowledgeSource,
    RunStatus,
    SourceKind,
    User,
    UserRole,
    new_id,
    normalize_username,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
EMBEDDING_DIM = 3
# Last revision before session storage; existing deployments upgrade from it.
REVISION_BEFORE_SESSIONS = "3665f6870fa4"


class ModelUnitTest(unittest.TestCase):
    def test_new_ids_are_uuid_v7(self) -> None:
        first, second = new_id(), new_id()

        self.assertIsInstance(first, UUID)
        self.assertEqual(first.version, 7)
        self.assertNotEqual(first, second)

    def test_normalize_username_trims_and_lowercases(self) -> None:
        self.assertEqual(normalize_username("  Nguyen.An "), "nguyen.an")

    def test_normalize_username_rejects_blank_names(self) -> None:
        with self.assertRaises(ValueError):
            normalize_username("   ")

    def test_models_cover_the_proposed_tables(self) -> None:
        self.assertEqual(
            set(Base.metadata.tables),
            {
                "users",
                "auth_sessions",
                "machines",
                "files",
                "knowledge_bases",
                "knowledge_sources",
                "knowledge_chunks",
                "knowledge_chunk_sources",
                "conversations",
                "inquiries",
                "inquiry_events",
                "inquiry_files",
                "ingestion_jobs",
                "extraction_runs",
                "content_signatures",
                "knowledge_conflicts",
            },
        )


def alembic_config(url: str) -> Config:
    """Build an Alembic configuration that migrates the given database."""

    # Collects command output such as `alembic check` messages.
    config = Config(toml_file=str(PROJECT_ROOT / "pyproject.toml"), stdout=io.StringIO())
    config.attributes["database_url"] = url
    config.attributes["configure_logging"] = False
    return config


class DisposableDatabase:
    """Creates and drops one uniquely named database on the test server."""

    def __init__(self, server_url: str) -> None:
        self.name = f"test_migrations_{uuid4().hex[:12]}"
        self._admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
        self.url = (
            make_url(server_url)
            .set(database=self.name)
            .render_as_string(hide_password=False)
        )

    def create(self) -> None:
        with self._admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{self.name}"'))

    def drop(self) -> None:
        try:
            with self._admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'))
        finally:
            self._admin.dispose()


class MigratedDatabaseTest(unittest.TestCase):
    """Base class: a disposable database upgraded to the latest revision."""

    database: DisposableDatabase
    config: Config
    engine: Engine

    @classmethod
    def setUpClass(cls) -> None:
        cls.database = DisposableDatabase(DATABASE_URL or "")
        cls.database.create()
        try:
            cls.config = alembic_config(cls.database.url)
            with mock.patch.dict(os.environ, {"EMBEDDING_DIM": str(EMBEDDING_DIM)}):
                command.upgrade(cls.config, "head")
            cls.engine = create_engine(cls.database.url)
        except BaseException:
            cls.database.drop()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls.database.drop()


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class MigrationRevisionTest(MigratedDatabaseTest):
    def test_models_match_the_migrated_schema(self) -> None:
        # Raises AutogenerateDiffsDetected when models and revisions differ.
        command.check(self.config)

    def test_check_constraints_match_the_models(self) -> None:
        inspector = inspect(self.engine)
        # Applies the naming convention, as when the models render DDL.
        preparer = self.engine.dialect.identifier_preparer
        for table in Base.metadata.sorted_tables:
            with self.subTest(table=table.name):
                expected = {
                    preparer.format_constraint(constraint)
                    for constraint in table.constraints
                    if isinstance(constraint, CheckConstraint)
                }
                actual = {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints(table.name)
                }
                self.assertEqual(actual, expected)

    def test_embedding_uses_the_configured_size(self) -> None:
        with self.engine.connect() as conn:
            size = conn.scalar(
                text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'knowledge_chunks'::regclass "
                    "AND attname = 'embedding'"
                )
            )
        self.assertEqual(size, EMBEDDING_DIM)

    def test_downgrade_removes_tables_and_upgrade_restores_them(self) -> None:
        database = DisposableDatabase(DATABASE_URL or "")
        database.create()
        try:
            config = alembic_config(database.url)
            with mock.patch.dict(os.environ, {"EMBEDDING_DIM": str(EMBEDDING_DIM)}):
                command.upgrade(config, "head")
                command.downgrade(config, "base")
                engine = create_engine(database.url)
                try:
                    self.assertEqual(inspect(engine).get_table_names(), ["alembic_version"])
                finally:
                    engine.dispose()
                command.upgrade(config, "head")
            command.check(config)
        finally:
            database.drop()

    def test_upgrade_refuses_the_service_job_queue_database(self) -> None:
        database = DisposableDatabase(DATABASE_URL or "")
        database.create()
        try:
            engine = create_engine(database.url)
            try:
                with engine.begin() as conn:
                    conn.execute(text("CREATE TABLE ingestion_jobs (id bigserial PRIMARY KEY, kb text)"))
                with mock.patch.dict(os.environ, {"EMBEDDING_DIM": str(EMBEDDING_DIM)}):
                    with self.assertRaisesRegex(RuntimeError, "APP_DATABASE_URL"):
                        command.upgrade(alembic_config(database.url), "head")
                self.assertEqual(inspect(engine).get_table_names(), ["ingestion_jobs"])
            finally:
                engine.dispose()
        finally:
            database.drop()


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class SessionStorageUpgradeTest(unittest.TestCase):
    """Upgrading a database that already holds accounts keeps them unchanged."""

    def test_upgrade_preserves_existing_users(self) -> None:
        database = DisposableDatabase(DATABASE_URL or "")
        database.create()
        try:
            config = alembic_config(database.url)
            engine = create_engine(database.url)
            try:
                with mock.patch.dict(os.environ, {"EMBEDDING_DIM": str(EMBEDDING_DIM)}):
                    command.upgrade(config, REVISION_BEFORE_SESSIONS)
                    # Stored hashes are opaque to the migration; formats vary on purpose.
                    accounts = [
                        (new_id(), f"{role.value}-user", role.value, password_hash)
                        for role, password_hash in (
                            (UserRole.EMPLOYEE, "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"),
                            (UserRole.EXPERT, "$2b$12$legacyBcryptHashValueKeptAsIs"),
                            (UserRole.ADMIN, "pbkdf2_sha256$600000$salt$hash"),
                        )
                    ]
                    with engine.begin() as conn:
                        for user_id, username, role, password_hash in accounts:
                            conn.execute(
                                text(
                                    "INSERT INTO users (id, username, display_name, password_hash, role) "
                                    "VALUES (:id, :username, :username, :password_hash, :role)"
                                ),
                                {"id": user_id, "username": username, "password_hash": password_hash, "role": role},
                            )

                    command.upgrade(config, "head")
                    command.check(config)
                    # Rerunning the upgrade is a no-op.
                    command.upgrade(config, "head")

                    with engine.connect() as conn:
                        rows = conn.execute(
                            text("SELECT id, username, role, password_hash FROM users ORDER BY username")
                        ).all()
                    self.assertEqual(
                        [tuple(row) for row in rows],
                        sorted(accounts, key=lambda account: account[1]),
                    )
                    self.assertIn("auth_sessions", inspect(engine).get_table_names())

                    # The revision before auth sessions were added.
                    command.downgrade(config, "3665f6870fa4")
                    self.assertNotIn("auth_sessions", inspect(engine).get_table_names())
                    command.upgrade(config, "head")
                    command.check(config)
                    with engine.connect() as conn:
                        self.assertEqual(conn.scalar(text("SELECT count(*) FROM users")), len(accounts))
            finally:
                engine.dispose()
        finally:
            database.drop()


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class DatabaseRuleTest(MigratedDatabaseTest):
    def setUp(self) -> None:
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)

    def add_user(self, role: UserRole = UserRole.EMPLOYEE) -> User:
        name = f"user-{uuid4().hex[:8]}"
        user = User(
            username=name,
            display_name=name,
            password_hash="not-a-real-hash",
            role=role.value,
        )
        self.session.add(user)
        self.session.commit()
        return user

    def add_collection(self, owner: User) -> KnowledgeBase:
        collection = KnowledgeBase(
            name=f"kb_{uuid4().hex[:8]}",
            title="Collection",
            template="general",
            created_by=owner.id,
        )
        self.session.add(collection)
        self.session.commit()
        return collection

    def add_text_source(self, collection: KnowledgeBase, owner: User) -> KnowledgeSource:
        source = KnowledgeSource(
            knowledge_base_id=collection.id,
            title="Shift note",
            kind=SourceKind.TEXT.value,
            submitted_by=owner.id,
        )
        self.session.add(source)
        self.session.commit()
        return source

    def add_chunk(self, collection: KnowledgeBase) -> KnowledgeChunk:
        chunk = KnowledgeChunk(
            knowledge_base_id=collection.id,
            title="Sensor fault",
            content="Clean the sensor lens before replacement.",
            search_text="Sensor fault: clean the sensor lens before replacement.",
            embedding=[0.1, 0.2, 0.3],
        )
        self.session.add(chunk)
        self.session.commit()
        return chunk

    def add_inquiry(self, requester: User, expert: User) -> Inquiry:
        conversation = Conversation(owner_id=requester.id, title="Line 3 stop")
        self.session.add(conversation)
        self.session.flush()
        inquiry = Inquiry(
            conversation_id=conversation.id,
            requested_by=requester.id,
            assigned_to=expert.id,
            title="Line 3 stop",
            context={"situation": "Conveyor stops after start-up."},
        )
        self.session.add(inquiry)
        self.session.commit()
        return inquiry

    def add_file(self, owner: User) -> File:
        upload = File(
            filename="photo.png",
            content_type="image/png",
            size_bytes=10,
            storage_key=f"uploads/{uuid4().hex}",
            sha256="0" * 64,
            uploaded_by=owner.id,
        )
        self.session.add(upload)
        self.session.commit()
        return upload

    def add_job(self, source: KnowledgeSource, **values) -> IngestionJob:
        defaults = {
            "knowledge_base_id": source.knowledge_base_id,
            "stage": JobStage.EXTRACT.value,
            "mode": ExtractionMode.KNOWLEDGE.value,
        }
        job = IngestionJob(source_id=source.id, **(defaults | values))
        self.session.add(job)
        self.session.commit()
        return job

    def add_run(self, source: KnowledgeSource, status: RunStatus) -> ExtractionRun:
        run = ExtractionRun(
            knowledge_base_id=source.knowledge_base_id,
            source_id=source.id,
            content_sha256="a" * 64,
            mode=ExtractionMode.KNOWLEDGE.value,
            fingerprint="f" * 64,
            status=status.value,
        )
        self.session.add(run)
        self.session.commit()
        return run

    def conflict(self, collection: KnowledgeBase, a_id: UUID, b_id: UUID, **values) -> KnowledgeConflict:
        defaults = {"a_hash": "a", "b_hash": "b", "judge_fingerprint": "judge-v1", "status": "open"}
        return KnowledgeConflict(
            knowledge_base_id=collection.id, a_id=a_id, b_id=b_id, **(defaults | values)
        )

    def assert_rejected(self, statement) -> None:
        with self.assertRaises(IntegrityError):
            self.session.execute(statement)
            self.session.flush()
        self.session.rollback()

    def add_session(self, user: User, **values) -> AuthSession:
        now = datetime.now(timezone.utc)
        auth_session = AuthSession(
            user_id=user.id,
            access_token_digest=values.pop("access_token_digest", uuid4().hex * 2),
            refresh_token_digest=values.pop("refresh_token_digest", uuid4().hex * 2),
            access_expires_at=now + timedelta(minutes=15),
            expires_at=now + timedelta(hours=72),
            **values,
        )
        self.session.add(auth_session)
        self.session.commit()
        return auth_session

    def test_session_token_digests_are_unique_sha256_values(self) -> None:
        user = self.add_user()
        first = self.add_session(user)

        for values in (
            {"access_token_digest": first.access_token_digest},
            {"refresh_token_digest": first.refresh_token_digest},
            {"access_token_digest": "raw-token"},
        ):
            with self.subTest(values=list(values)):
                with self.assertRaises(IntegrityError):
                    self.add_session(user, **values)
                self.session.rollback()

    def test_session_updates_set_updated_at(self) -> None:
        auth_session = self.add_session(self.add_user())
        before = auth_session.updated_at

        auth_session.revoked_at = datetime.now(timezone.utc)
        self.session.commit()

        self.assertGreater(auth_session.updated_at, before)

    def test_usernames_must_be_stored_normalized(self) -> None:
        self.session.add(
            User(
                username=" Mixed.Case ",
                display_name="Mixed",
                password_hash="not-a-real-hash",
                role=UserRole.EMPLOYEE.value,
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_unknown_roles_are_rejected(self) -> None:
        user = self.add_user()
        self.assert_rejected(update(User).where(User.id == user.id).values(role="owner"))

    def test_update_sets_updated_at_and_keeps_created_at(self) -> None:
        user = self.add_user()
        before = user.updated_at

        user.display_name = "Renamed"
        self.session.commit()

        self.assertGreater(user.updated_at, before)
        self.assert_rejected(
            update(User).where(User.id == user.id).values(created_at=text("now() - interval '1 day'"))
        )

    def test_document_sources_require_a_file(self) -> None:
        user = self.add_user()
        collection = self.add_collection(user)
        self.session.add(
            KnowledgeSource(
                knowledge_base_id=collection.id,
                title="Manual",
                kind=SourceKind.DOCUMENT.value,
                submitted_by=user.id,
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_evidence_links_stay_within_one_collection(self) -> None:
        user = self.add_user(UserRole.EXPERT)
        first, second = self.add_collection(user), self.add_collection(user)
        chunk = self.add_chunk(first)
        same_collection_source = self.add_text_source(first, user)
        other_collection_source = self.add_text_source(second, user)
        references = [{"quote": "clean the sensor lens"}]

        self.session.add(
            KnowledgeChunkSource(
                chunk_id=chunk.id, source_id=same_collection_source.id, references=references
            )
        )
        self.session.commit()

        self.session.add(
            KnowledgeChunkSource(
                chunk_id=chunk.id, source_id=other_collection_source.id, references=references
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()
        self.assert_rejected(
            update(KnowledgeChunk)
            .where(KnowledgeChunk.id == chunk.id)
            .values(knowledge_base_id=second.id)
        )

    def test_evidence_links_need_at_least_one_reference(self) -> None:
        user = self.add_user(UserRole.EXPERT)
        collection = self.add_collection(user)
        self.session.add(
            KnowledgeChunkSource(
                chunk_id=self.add_chunk(collection).id,
                source_id=self.add_text_source(collection, user).id,
                references=[],
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_changed_search_text_clears_the_old_embedding(self) -> None:
        user = self.add_user(UserRole.EXPERT)
        chunk = self.add_chunk(self.add_collection(user))

        self.session.execute(
            update(KnowledgeChunk)
            .where(KnowledgeChunk.id == chunk.id)
            .values(search_text="Sensor fault: check the connector first.")
        )
        self.session.commit()

        embedding, matches = self.session.execute(
            select(
                KnowledgeChunk.embedding,
                KnowledgeChunk.search_vector.op("@@")(
                    text("plainto_tsquery('simple', 'connector')")
                ),
            ).where(KnowledgeChunk.id == chunk.id)
        ).one()
        self.assertIsNone(embedding)
        self.assertTrue(matches)

    def test_requester_must_own_the_conversation(self) -> None:
        owner, other, expert = self.add_user(), self.add_user(), self.add_user(UserRole.EXPERT)
        inquiry = self.add_inquiry(owner, expert)

        self.session.add(
            Inquiry(
                conversation_id=inquiry.conversation_id,
                requested_by=other.id,
                assigned_to=expert.id,
                title="Copied inquiry",
                context={},
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_inquiry_events_are_permanent(self) -> None:
        requester, expert = self.add_user(), self.add_user(UserRole.EXPERT)
        inquiry = self.add_inquiry(requester, expert)
        event = InquiryEvent(
            inquiry_id=inquiry.id,
            actor_id=expert.id,
            kind=InquiryEventKind.QUESTION.value,
            content="Which error code is shown?",
        )
        self.session.add(event)
        self.session.commit()

        self.assert_rejected(
            update(InquiryEvent).where(InquiryEvent.id == event.id).values(content="Edited")
        )
        self.assert_rejected(text("DELETE FROM inquiry_events WHERE id = :id").bindparams(id=event.id))

    def test_questions_require_content(self) -> None:
        requester, expert = self.add_user(), self.add_user(UserRole.EXPERT)
        inquiry = self.add_inquiry(requester, expert)
        self.session.add(
            InquiryEvent(
                inquiry_id=inquiry.id,
                actor_id=expert.id,
                kind=InquiryEventKind.QUESTION.value,
                content="  ",
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_attachment_event_must_belong_to_the_inquiry(self) -> None:
        requester, expert = self.add_user(), self.add_user(UserRole.EXPERT)
        first = self.add_inquiry(requester, expert)
        second = self.add_inquiry(requester, expert)
        event = InquiryEvent(
            inquiry_id=second.id,
            actor_id=requester.id,
            kind=InquiryEventKind.REPLY.value,
            content="Photo attached.",
        )
        self.session.add(event)
        self.session.commit()

        self.session.add(
            InquiryFile(
                inquiry_id=first.id, file_id=self.add_file(requester).id, event_id=event.id
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_one_active_job_per_source(self) -> None:
        user = self.add_user()
        source = self.add_text_source(self.add_collection(user), user)
        first = self.add_job(source)

        with self.assertRaises(IntegrityError):
            self.add_job(source)
        self.session.rollback()

        before = first.updated_at
        first.status = JobStatus.DONE.value
        self.session.commit()
        self.assertGreater(first.updated_at, before)
        self.add_job(source, parent_id=first.id)

    def test_job_collection_must_match_its_source(self) -> None:
        user = self.add_user()
        source = self.add_text_source(self.add_collection(user), user)
        other = self.add_collection(user)

        with self.assertRaises(IntegrityError):
            self.add_job(source, knowledge_base_id=other.id)

    def test_active_extraction_runs_are_unique_per_collection(self) -> None:
        user = self.add_user()
        first_collection, second_collection = self.add_collection(user), self.add_collection(user)
        source = self.add_text_source(first_collection, user)
        self.add_run(source, RunStatus.FAILED)
        self.add_run(source, RunStatus.DONE)
        # The same content may be extracted once in each collection.
        self.add_run(self.add_text_source(second_collection, user), RunStatus.RUNNING)

        with self.assertRaises(IntegrityError):
            self.add_run(self.add_text_source(first_collection, user), RunStatus.RUNNING)

    def test_conflict_pairs_are_ordered_within_one_collection(self) -> None:
        user = self.add_user(UserRole.EXPERT)
        collection, other = self.add_collection(user), self.add_collection(user)
        low, high = sorted([self.add_chunk(collection).id, self.add_chunk(collection).id])
        outside = self.add_chunk(other).id

        self.session.add(self.conflict(collection, low, high))
        self.session.commit()

        for a_id, b_id, values in (
            (high, low, {}),
            (min(low, outside), max(low, outside), {}),
            (low, high, {"judge_fingerprint": "judge-v2", "review": {}}),
        ):
            with self.subTest(a_id=a_id, b_id=b_id, values=values):
                self.session.add(self.conflict(collection, a_id, b_id, **values))
                with self.assertRaises(IntegrityError):
                    self.session.commit()
                self.session.rollback()


if __name__ == "__main__":
    unittest.main()

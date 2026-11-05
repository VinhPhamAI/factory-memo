"""Phase 7 HTTP workflows against a disposable application database."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from unittest import mock, skipUnless
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app import create_app
from backend.src.services.application_source_pipeline import ApplicationSourcePipeline
from backend.src.services.auth import Account, InvalidSession
from backend.src.services.job_worker import StageWorker
from backend.src.storage.document_uploads import DocumentUploadStore
from backend.src.storage.models import KnowledgeRecord, KnowledgeType
from backend.src.storage.orm import ExtractionRun, File, IngestionJob, KnowledgeChunk, KnowledgeSource, SourceKind, User
from backend.src.storage.repositories.knowledge_conflicts import ConflictRepository
from backend.tests.test_database_migrations import DATABASE_URL, EMBEDDING_DIM, MigratedDatabaseTest


class TestAccounts:
    """Provide deterministic authentication while exercising the real API storage."""

    def __init__(self, accounts: dict[str, Account]) -> None:
        self.accounts = accounts

    async def authenticate_access(self, token: str) -> Account:
        """Return the fixture account for its bearer token."""

        if token not in self.accounts:
            raise InvalidSession()
        return self.accounts[token]


class ConcurrentUploadStore(DocumentUploadStore):
    """Let both original objects reach disk before either upload inserts its row."""

    barrier: Barrier | None = None

    def save(self, *args, **kwargs):
        """Synchronize exactly the two uploads in the concurrency check."""

        document = super().save(*args, **kwargs)
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        return document


@skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class ApplicationConflictApiTest(MigratedDatabaseTest):
    """Exercise conflict handling against a migrated application database."""

    def test_application_conflict_workflow(self) -> None:
        """Queue extraction and review its conflicts with authenticated requests."""

        with Session(self.engine) as session, session.begin():
            expert = User(username="conflict_expert", display_name="Expert",
                          password_hash="test", role="expert")
            session.add(expert)
            session.flush()
            account = Account(expert.id, expert.username, expert.display_name, expert.role)
        headers = {"Authorization": "Bearer expert"}
        self.enterContext(mock.patch.dict(os.environ, {
            "APP_DATABASE_URL": self.database.url, "EMBEDDING_DIM": str(EMBEDDING_DIM),
            "DATABASE_URL": "unused-legacy-database",
        }))
        api = self.enterContext(TestClient(create_app(auth_service=TestAccounts({
            "expert": account,
            "employee": Account(account.id, "worker", "Worker", "employee"),
        }))))
        response = api.post("/v1/knowledge-bases", headers=headers, json={
            "name": "conflict_factory", "title": "Factory conflict test",
        })
        self.assertEqual(response.status_code, 201, response.text)
        kb_id, _spec = api.app.state.kb_catalog.resolve("conflict_factory")
        manager = api.app.state.knowledge_management
        old = KnowledgeRecord(
            subject="Pump seal", claim="Wait 10 seconds before restarting.",
            type=KnowledgeType.RULE, embedding=[1.0, 0.0, 0.0],
        )
        new = KnowledgeRecord(
            subject="Pump seal", claim="Wait 30 seconds before restarting.",
            type=KnowledgeType.RULE, embedding=[1.0, 0.0, 0.0],
        )

        async def seed_sources() -> UUID:
            """Save one previously extracted record and one input for the extract worker."""

            prior = await manager.sources.create(
                kb_id, kind=SourceKind.TEXT, title="Prior record",
                submitted_by=account.id, extracted_text=old.claim,
            )
            await manager.chunks.add_records(kb_id, prior.id, [old])
            source = await manager.sources.create(
                kb_id, kind=SourceKind.TEXT, title="New input",
                submitted_by=account.id, extracted_text=new.claim,
            )
            return source.id

        def extraction_factory(_spec, store):
            """Supply one controlled extraction through the real worker write path."""

            async def ingest(_conversation):
                """Return one extracted record and let the worker persist it."""

                await store.add_knowledge([new])
                return SimpleNamespace(records=[new], merged=[], consolidation={})

            return SimpleNamespace(ingest_stored=ingest), {"version": "fixture"}

        source_id = api.portal.call(seed_sources)
        base = "/v1/knowledge-management/conflict_factory"
        source_path = f"{base}/sources/conversation/{source_id}"
        conflicts_path = f"{base}/conflicts"
        self.assertEqual(api.get(conflicts_path).status_code, 401)
        self.assertTrue(api.get(conflicts_path, headers=headers).json()["enabled"])
        queued = api.post(f"{source_path}/extract", headers=headers, json={"mode": "knowledge"})
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual(queued.json()["job"]["next_stages"], ["conflict"])

        pipeline = ApplicationSourcePipeline(
            api.app.state.kb_catalog, manager.sources, manager.chunks,
            api.app.state.document_uploads, manager.sessions,
            ingestion_factory=extraction_factory, embeddings_factory=lambda **_: None,
            vector_size=EMBEDDING_DIM,
        )
        self.addCleanup(api.portal.call, pipeline.aclose)
        extract = StageWorker(manager.jobs, pipeline, ["extract"])
        self.assertEqual(api.portal.call(extract.drain), 1)
        detail = api.get(source_path, headers=headers).json()
        self.assertEqual(detail["processing"]["status"], "done")
        self.assertEqual(detail["processing"]["conflict_job"]["status"], "queued")
        self.assertEqual(detail["conversation"]["status"], "processed")

        judge = mock.Mock(ainvoke=mock.AsyncMock(return_value=SimpleNamespace(content=json.dumps({
            "verdicts": [{
                "candidate": "E1", "relation": "contradiction", "aspect": "value",
                "quote_new": new.claim, "quote_old": old.claim, "confidence": 0.95,
            }],
        }))))
        worker = StageWorker(manager.jobs, pipeline, ["conflict"])
        with mock.patch("backend.knowledge_bases.general.conflicts.llm.chat_client_from_env",
                        return_value=judge):
            self.assertEqual(api.portal.call(worker.drain), 1)
        self.assertEqual(judge.ainvoke.await_count, 2)
        detail = api.get(source_path, headers=headers).json()
        self.assertEqual(detail["processing"]["conflicts"]["open"], 1)
        response = api.get(conflicts_path, headers=headers, params={"status": "open", "evidence_id": str(source_id)})
        self.assertEqual(response.status_code, 200, response.text)
        pair, = response.json()["conflicts"]
        self.assertTrue(pair["linked"])
        review_path = f"{conflicts_path}/{pair['id']}/review"
        response = api.post(review_path, headers={"Authorization": "Bearer employee"}, json={"decision": "confirm"})
        self.assertEqual(response.status_code, 403)

        with mock.patch.object(ConflictRepository, "set_review", side_effect=OSError("simulated write failure")):
            response = api.post(review_path, headers=headers, json={"decision": "dismiss"})
        self.assertEqual(response.status_code, 503)
        pair, = api.get(conflicts_path, headers=headers).json()["conflicts"]
        self.assertTrue(pair["linked"])
        self.assertEqual(pair["a"]["conflict_with"], [pair["b_id"]])
        self.assertEqual(pair["review"], [])

        for decision, status in (("dismiss", "dismissed"), ("confirm", "confirmed")):
            response = api.post(review_path, headers=headers, json={"decision": decision, "note": "Checked source"})
            self.assertEqual(response.status_code, 200, response.text)
            pair = response.json()
            self.assertEqual(pair["status"], status)
            self.assertEqual({pair["a"]["status"], pair["b"]["status"]}, {"draft"})
            self.assertEqual(pair["linked"], decision == "confirm")

        with self.engine.begin() as connection:
            connection.execute(update(KnowledgeChunk).where(KnowledgeChunk.id == new.id)
                               .values(content=new.claim + " After inspection."))
        response = api.post(review_path, headers=headers, json={"decision": "dismiss"})
        self.assertEqual(response.status_code, 409, response.text)
        response = api.post(f"{conflicts_path}/recheck", headers=headers, json={})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["sources"], 1)
        with mock.patch("backend.knowledge_bases.general.conflicts.llm.chat_client_from_env",
                        return_value=judge):
            self.assertEqual(api.portal.call(worker.drain), 1)
        response = api.post(review_path, headers=headers, json={
            "decision": "supersede", "keep_id": pair["a_id"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        pair = response.json()
        self.assertEqual((pair["status"], pair["a"]["status"], pair["b"]["status"]),
                         ("resolved", "draft", "deprecated"))
        self.assertEqual(pair["b"]["superseded_by"], pair["a_id"])
        self.assertEqual([event["decision"] for event in pair["review"]],
                         ["dismiss", "confirm", "supersede"])


@skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class Phase7ApiTest(MigratedDatabaseTest):
    """Exercise routes, cleanup, access control and catalog invalidation."""

    def test_rename_duplicate_remove_and_delete(self) -> None:
        """Run the management flow with two API instances and a shared database."""

        accounts = {}
        with Session(self.engine) as session, session.begin():
            for username, role in (
                ("worker", "employee"), ("other", "employee"),
                ("expert", "expert"), ("admin", "admin"),
            ):
                user = User(username=username, display_name=username, password_hash="test", role=role)
                session.add(user)
                session.flush()
                accounts[username] = Account(user.id, username, username, role)

        def headers(token: str) -> dict[str, str]:
            return {"Authorization": f"Bearer {token}"}

        admin, expert, employee, other = map(headers, ("admin", "expert", "worker", "other"))
        self.enterContext(mock.patch.dict(os.environ, {
            "APP_DATABASE_URL": self.database.url, "EMBEDDING_DIM": str(EMBEDDING_DIM),
        }))
        store = ConcurrentUploadStore(Path(self.enterContext(TemporaryDirectory())))
        auth = TestAccounts(accounts)
        api = self.enterContext(TestClient(create_app(auth_service=auth, document_uploads=store)))
        qa = self.enterContext(TestClient(create_app(auth_service=auth, document_uploads=store)))

        kb_path = "/v1/knowledge-bases/factory"
        self.assertEqual(api.post("/v1/knowledge-bases", headers=expert, json={
            "name": "factory", "title": "Factory",
        }).status_code, 201)
        self.assertEqual(qa.get(kb_path, headers=employee).json()["created_by"], "expert")
        self.assertEqual(api.patch(kb_path, headers=expert, json={"title": "Line A"}).status_code, 200)
        self.assertEqual(qa.get("/v1/knowledge-bases", headers=employee)
                         .json()["knowledge_bases"][0]["title"], "Line A")

        def upload(filename: str, content: bytes = b"Pump inspection"):
            """Upload a named text file through the public API."""

            return api.post("/v1/documents/factory/uploads", headers=employee,
                            files={"file": (filename, content, "text/plain")})

        store.barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.map(upload, ("manual.txt", "renamed.txt"))
        store.barrier = None
        self.assertEqual(sorted((first.status_code, second.status_code)), [200, 201])
        created, duplicate = (first, second) if first.status_code == 201 else (second, first)
        original = created.json()
        self.assertEqual(duplicate.json(), {**original, "duplicate": True,
                                           "uploaded_at": duplicate.json()["uploaded_at"]})
        self.assertEqual(len(list(store.root.glob("factory/*/content"))), 1)
        self.assertEqual(api.get(kb_path, headers=employee).json()["source_counts"]["received"], 1)

        inventory = "/v1/knowledge-management/factory/sources"
        source = f"{inventory}/upload/{original['id']}"
        self.assertEqual(api.delete(source, headers=other).status_code, 403)
        self.assertEqual(api.get(f"{source}/original", headers=employee).content, b"Pump inspection")
        self.assertEqual(api.post(f"{source}/convert", headers=employee).status_code, 202)
        jobs = api.app.state.knowledge_management.jobs
        running = api.portal.call(jobs.claim, "convert", "test-worker")
        self.assertEqual(api.delete(source, headers=employee).status_code, 409)
        api.portal.call(jobs.release, running, "test-worker")
        with Session(self.engine) as session, session.begin():
            row = session.get(KnowledgeSource, UUID(original["id"]))
            session.add(ExtractionRun(
                knowledge_base_id=row.knowledge_base_id, source_id=row.id,
                original_sha256=row.original_sha256, content_sha256=row.original_sha256,
                mode="knowledge", fingerprint="fixture", status="done", result={"stored": 1},
            ))
        blocked = api.post(f"{source}/extract", headers=employee, json={"mode": "knowledge"})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "duplicate_content")

        # Simulate a copy whose duplicate became visible after the extract job was queued.
        copy = upload("copy.txt", b"Pump inspection\n")
        self.assertEqual(copy.status_code, 201, copy.text)
        copy_id = UUID(copy.json()["id"])
        copy_path = f"{inventory}/upload/{copy_id}"
        manager = api.app.state.knowledge_management
        kb_id, _ = api.app.state.kb_catalog.resolve("factory")

        async def check_copy() -> None:
            """Run the worker's duplicate check on an already queued copy."""

            await manager.sources.update(kb_id, copy_id, extracted_text="Pump inspection\n")
            await jobs.enqueue(kb_id, copy_id, "extract", mode="knowledge")
            pipeline = ApplicationSourcePipeline(
                api.app.state.kb_catalog, manager.sources, manager.chunks, store, manager.sessions,
            )
            try:
                self.assertEqual(await StageWorker(jobs, pipeline, ["extract"]).drain(), 1)
            finally:
                await pipeline.aclose()

        api.portal.call(check_copy)
        checked_copy = api.get(copy_path, headers=employee).json()
        self.assertEqual(checked_copy["processing"]["status"], "duplicate")
        self.assertEqual(checked_copy["stage"], "converted")

        self.assertEqual(api.delete(source, headers=employee).status_code, 204)
        self.assertEqual(api.get(f"{source}/original", headers=employee).status_code, 404)
        self.assertEqual([item["id"] for item in api.get(inventory, headers=employee).json()["sources"]],
                         [str(copy_id)])
        removed = api.get(inventory, headers=employee, params={"include_removed": "true"}).json()["sources"]
        removed_original = next(item for item in removed if UUID(item["id"]) == UUID(original["id"]))
        self.assertIsNotNone(removed_original["removed_at"])
        self.assertTrue((store.root / original["storage_key"]).is_file())
        with Session(self.engine) as session:
            row = session.get(KnowledgeSource, UUID(original["id"]))
            self.assertIsNotNone(session.get(File, row.file_id).deleted_at)
            self.assertEqual(session.scalar(select(IngestionJob.error).where(
                IngestionJob.source_id == row.id,
            )), "SOURCE_REMOVED")

        reupload = upload("manual.txt")
        self.assertEqual(reupload.status_code, 201, reupload.text)
        self.assertNotEqual(reupload.json()["id"], original["id"])
        replacement = f"{inventory}/upload/{reupload.json()['id']}"
        allowed = api.post(f"{replacement}/extract", headers=employee, json={"mode": "knowledge"})
        self.assertEqual(allowed.status_code, 202, allowed.text)
        self.assertEqual(allowed.json()["job"]["stage"], "convert")
        retry = api.post(f"{copy_path}/extract", headers=employee, json={"mode": "knowledge"})
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(retry.json()["job"]["stage"], "extract")
        self.assertEqual(api.delete(kb_path, headers=expert).status_code, 403)
        self.assertEqual(api.delete(kb_path, headers=admin).status_code, 204)
        self.assertEqual(api.delete(kb_path, headers=admin).status_code, 404)
        self.assertEqual(qa.get(kb_path, headers=employee).status_code, 404)
        self.assertEqual(qa.get("/v1/knowledge-agent/knowledge-bases", headers=employee)
                         .json()["knowledge_bases"], [])
        self.assertEqual(qa.post("/v1/knowledge-agent/factory/stream", headers=employee,
                                 json={"query": "Pump?", "thread_id": "phase7"}).status_code, 404)
        self.assertEqual(api.post("/v1/knowledge-bases", headers=expert, json={
            "name": "factory", "title": "Name is reserved",
        }).status_code, 409)

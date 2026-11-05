"""Chunk search: repository ranking and the `POST /v1/knowledge-management/{kb}/search` API.

The integration tests need PostgreSQL + pgvector; see `test_database_migrations.py`:
    TEST_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent \
        uv run python -m unittest backend.tests.test_chunk_search_api -v

The migrated databases use `EMBEDDING_DIM=3`; query embeddings are 3-dimensional too.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.orm import Session

from backend.api.auth import require_current_user
from backend.api.knowledge_search import router as search_router
from backend.app import create_app
from backend.src.agents.rag.store import SearchMode
from backend.src.services.auth import Account
from backend.src.storage import KnowledgeRecord
from backend.src.storage.chunk_retrieval import ChunkRetrieval
from backend.src.storage.embeddings import EmbeddingMismatch, EmbeddingUnavailable
from backend.src.storage.knowledge_store import build_knowledge_filter
from backend.src.storage.orm import (
    File, KnowledgeChunk, KnowledgeChunkSource, KnowledgeSource, SourceKind, User,
)
from backend.src.storage.repositories import ExtractionRunRepository, KnowledgeStorageUnavailable, NewFile
from backend.src.storage.repositories.chunks import _lexical_candidates
from backend.tests.test_chunk_retrieval import OTHER_VECTOR, QUERY_VECTOR, FakeEmbeddings, RetrievalTest
from backend.tests.test_database_migrations import DATABASE_URL, EMBEDDING_DIM, MigratedDatabaseTest
from backend.tests.test_file_types import PDF
from backend.tests.test_phase7_knowledge_management import TestAccounts


class ChunkApplicabilityTest(unittest.TestCase):
    """Verify how public applicability is derived from stored chunk details."""

    def test_allowlists_bounds_and_ignores_invalid_context(self) -> None:
        """Only publish recognized nonempty strings from a context object."""

        from backend.src.storage.repositories.chunks import _search_applicability

        self.assertEqual(_search_applicability({"context": {
            "applies_to": "  Pump P-101  ",
            "condition": "x" * 250,
            "private_note": "omit",
        }}), {"applies_to": "Pump P-101", "condition": "x" * 240})
        self.assertIsNone(_search_applicability({"context": {"applies_to": " ", "condition": ["dusty"]}}))
        self.assertIsNone(_search_applicability({"context": "invalid context"}))


class ChunkSearchErrorTest(unittest.TestCase):
    """Verify search error responses without starting PostgreSQL."""

    def test_repository_unavailable_returns_503(self) -> None:
        """Return a service error when storage fails after resolving the KB."""

        app = FastAPI()
        app.include_router(search_router)
        app.dependency_overrides[require_current_user] = lambda: object()
        app.state.knowledge_management = SimpleNamespace(spec=lambda _kb: (uuid4(), object()))
        app.state.chunk_search = SimpleNamespace(
            search=mock.AsyncMock(side_effect=KnowledgeStorageUnavailable("connection failed"))
        )
        with TestClient(app, raise_server_exceptions=False) as api:
            response = api.post("/v1/knowledge-management/line/search", json={"query": "seal"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Knowledge storage is unavailable"})


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class ChunkSearchRepositoryTest(RetrievalTest):
    def link_source(self, chunk: UUID, source: UUID) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(KnowledgeChunkSource).values(
                chunk_id=chunk, source_id=source, references=[{"type": "source"}]
            ))

    async def found(self, mode: SearchMode, retrieval: ChunkRetrieval | None = None, **options):
        """Chunk IDs of a search for the fixture text, in rank order."""

        hits = await (retrieval or self.retrieval).search_chunks(
            "bearing seal", mode=mode, limit=20, **options
        )
        return [hit.chunk_id for hit in hits]

    async def test_hybrid_knowledge_order_matches_the_agent_search(self) -> None:
        """Prefer a confident dense-only hit over a weak hit found by both channels."""

        source = await self.add_source()
        confident = await self.add_knowledge(source, claim="Replace the gasket.")
        weak = await self.add_knowledge(source, claim="Inspect the seal.", embedding=OTHER_VECTOR)
        agent = await self.retrieval.search_knowledge("bearing seal", k=20)
        ranked = await self.found(SearchMode.HYBRID, kinds=("knowledge",))
        self.assertEqual(ranked, [confident, weak])
        self.assertEqual(ranked, [hit.record.id for hit in agent])

    async def test_visibility_is_rechecked_after_candidate_selection(self) -> None:
        """Fill the result limit after dropping candidates that lose eligibility."""

        live = await self.add_source()
        removed = await self.add_source()
        kept = await self.add_knowledge(live, claim="Inspect the seal.", embedding=OTHER_VECTOR)
        rejected, deleted = [await self.add_knowledge(live) for _ in range(2)]
        unsourced = await self.add_knowledge(removed)
        initial = await self.retrieval.search_chunks("bearing seal", limit=1)
        self.assertIn(initial[0].chunk_id, {rejected, deleted, unsourced})

        async def select_then_revoke(*args):
            """Commit visibility changes after the lexical query has selected all candidates."""

            candidates, terms = await _lexical_candidates(*args)
            self.assertCountEqual(
                [chunk_id for chunk_id, _ in candidates], [kept, rejected, deleted, unsourced]
            )
            self.change(KnowledgeChunk, rejected, status="deprecated")
            self.soft_delete(KnowledgeChunk, deleted)
            self.soft_delete(KnowledgeSource, removed)
            return candidates, terms

        with mock.patch("backend.src.storage.repositories.chunks._lexical_candidates", select_then_revoke):
            (hit,) = await self.retrieval.search_chunks("bearing seal", limit=1)
        self.assertEqual(hit.chunk_id, kept)
        self.assertEqual([item.source_id for item in hit.evidence], [live])

    async def test_every_mode_excludes_deprecated_deleted_foreign_and_unsourced_chunks(self) -> None:
        live, gone = await self.add_source(), await self.add_source()
        kept = [await self.add_knowledge(live), await self.add_passage(live)]
        for add in (self.add_knowledge, self.add_passage):
            deprecated, deleted = await add(live), await add(live)
            self.change(KnowledgeChunk, deprecated, status="deprecated")
            self.soft_delete(KnowledgeChunk, deleted)
            await add(gone)
        self.soft_delete(KnowledgeSource, gone)
        other_kb = self.add_kb()
        other_source = await self.add_source(other_kb)
        await self.add_knowledge(other_source, kb=other_kb)
        await self.add_passage(other_source, kb=other_kb)
        superseded = await self.add_knowledge(live)
        self.change(KnowledgeChunk, superseded, superseded_by=kept[0])
        for mode in SearchMode:
            with self.subTest(mode=mode):
                self.assertCountEqual(await self.found(mode), kept)

    async def test_statuses_and_kinds_filter_the_results(self) -> None:
        source = await self.add_source()
        draft = await self.add_knowledge(source)
        approved = await self.add_knowledge(source)
        passage = await self.add_passage(source)
        self.change(KnowledgeChunk, approved, status="approved")
        self.assertCountEqual(await self.found(SearchMode.HYBRID), [draft, approved, passage])
        self.assertEqual(await self.found(SearchMode.HYBRID, statuses=("approved",)), [approved])
        self.assertCountEqual(
            await self.found(SearchMode.HYBRID, kinds=("document_passage",)), [passage]
        )
        with self.assertRaises(ValueError):
            await self.found(SearchMode.HYBRID, statuses=("deprecated",))

    async def test_scores_name_the_rankings_that_produced_the_hit(self) -> None:
        source = await self.add_source()
        chunk = await self.add_knowledge(source)
        expected = {
            SearchMode.HYBRID: (True, True, True),
            SearchMode.SEMANTIC: (False, True, False),
            SearchMode.KEYWORD: (False, False, True),
        }
        for mode, (fused, dense, keyword) in expected.items():
            with self.subTest(mode=mode):
                (hit,) = await self.retrieval.search_chunks("bearing seal", mode=mode)
                self.assertEqual(hit.chunk_id, chunk)
                self.assertEqual(
                    (hit.fused is not None, hit.dense is not None, hit.keyword is not None),
                    (fused, dense, keyword),
                )

    async def test_the_context_filter_keeps_the_unscoped_match_rule(self) -> None:
        source = await self.add_source()
        scoped = await self.add_knowledge(source, context={"equipment": "P-101"})
        await self.add_knowledge(source, context={"equipment": "P-202"})
        unscoped = await self.add_knowledge(source)
        ranked = await self.found(
            SearchMode.HYBRID, filters=build_knowledge_filter(context={"equipment": "P-101"})
        )
        self.assertCountEqual(ranked, [scoped, unscoped])

    async def test_keyword_search_needs_no_embedding_client_but_vector_search_does(self) -> None:
        source = await self.add_source()
        chunk = await self.add_knowledge(source)
        offline = ChunkRetrieval(
            self.kb.id, self.kb.name, self.chunks, self.sources, self.runs, None,
            vector_size=3, embedding_model="model-a",
        )
        self.assertEqual(await self.found(SearchMode.KEYWORD, offline), [chunk])
        for mode in (SearchMode.HYBRID, SearchMode.SEMANTIC):
            with self.assertRaises(EmbeddingUnavailable):
                await self.found(mode, offline)

    async def test_a_failing_embedding_provider_is_unavailable(self) -> None:
        class Broken(FakeEmbeddings):
            async def aembed_query(self, text: str) -> list[float]:
                raise ConnectionError("provider down")

        retrieval = ChunkRetrieval(
            self.kb.id, self.kb.name, self.chunks, self.sources, self.runs, Broken(),
            vector_size=3,
        )
        with self.assertRaises(EmbeddingUnavailable):
            await self.found(SearchMode.HYBRID, retrieval)

    async def test_a_model_mismatch_fails_vector_searches_only(self) -> None:
        source = await self.add_source()
        chunk = await self.add_knowledge(source)
        run = await self.runs.start(
            source_id=source, content="c" * 64, mode="knowledge", fingerprint="f",
            settings={"embedding_model": "model-a", "embedding_dim": 3},
        )
        await self.runs.finish(run.id, "done")
        other = self.retrieval_for(self.kb, model="model-b")
        for mode in (SearchMode.HYBRID, SearchMode.SEMANTIC):
            with self.assertRaises(EmbeddingMismatch) as caught:
                await self.found(mode, other)
            self.assertIn("model-a", str(caught.exception))
            self.assertIn("model-b", str(caught.exception))
        self.assertEqual(await self.found(SearchMode.KEYWORD, other), [chunk])

    async def test_results_only_include_active_evidence(self) -> None:
        """Keep a chunk with active links and omit its removed source and file evidence."""

        file_source, file_id = await self.add_file_source()
        text_source, removed = await self.add_source(), await self.add_source()
        chunk = await self.add_knowledge(file_source)
        self.link_source(chunk, text_source)
        self.link_source(chunk, removed)
        await self.sources.remove(self.kb.id, removed, actor_id=self.user.id, role=self.user.role)
        (hit,) = await self.retrieval.search_chunks("bearing seal", mode=SearchMode.KEYWORD)
        self.assertEqual(hit.chunk_id, chunk)
        self.assertCountEqual(
            [(item.source_id, item.has_file) for item in hit.evidence],
            [(file_source, True), (text_source, False)],
        )
        self.soft_delete(File, file_id)
        (hit,) = await self.retrieval.search_chunks("bearing seal", mode=SearchMode.KEYWORD)
        self.assertEqual([item.source_id for item in hit.evidence], [text_source])

    async def test_arguments_are_validated(self) -> None:
        for options in ({"limit": 0}, {"kinds": ()}, {"kinds": ("note",)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                await self.retrieval.search_chunks("bearing seal", **options)


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class ChunkSearchApiTest(MigratedDatabaseTest):
    """Search through the real API, repositories and database."""

    def setUp(self) -> None:
        accounts = {}
        with Session(self.engine) as session, session.begin():
            for username, role in (("worker", "employee"), ("expert", "expert")):
                user = User(
                    username=f"{username}-{uuid4().hex[:6]}", display_name=username,
                    password_hash="test", role=role,
                )
                session.add(user)
                session.flush()
                accounts[username] = Account(user.id, user.username, username, role)
        self.accounts = accounts
        self.worker = {"Authorization": "Bearer worker"}
        self.expert = {"Authorization": "Bearer expert"}
        storage = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {
            "APP_DATABASE_URL": self.database.url,
            "EMBEDDING_DIM": str(EMBEDDING_DIM),
            "EMBEDDING_MODEL": "model-a",
            "FILE_LIBRARY_DIR": str(storage / "library"),
            "DOCUMENT_UPLOAD_DIR": str(storage / "uploads"),
        }))
        self.embeddings = mock.patch(
            "backend.src.services.chunk_search.build_embeddings_from_env",
            return_value=FakeEmbeddings(),
        )
        self.build = self.enterContext(self.embeddings)
        self.api = self.enterContext(TestClient(create_app(auth_service=TestAccounts(accounts))))
        self.kb = f"kb_{uuid4().hex[:8]}"
        created = self.api.post(
            "/v1/knowledge-bases", headers=self.expert, json={"name": self.kb, "title": "Line"}
        )
        self.assertEqual(created.status_code, 201)
        self.path = f"/v1/knowledge-management/{self.kb}/search"
        self.manager = self.api.app.state.knowledge_management
        self.kb_id, _spec = self.manager.spec(self.kb)

    def call(self, function, *args, **kwargs):
        return self.api.portal.call(lambda: function(*args, **kwargs))

    def add_source(self, *, file: bool = False) -> UUID:
        source = self.call(
            self.manager.sources.create,
            self.kb_id,
            kind=SourceKind.DOCUMENT if file else SourceKind.TEXT,
            title="manual.txt" if file else "Note",
            submitted_by=self.accounts["expert"].id,
            file=NewFile(
                filename="manual.txt", content_type="text/plain", size_bytes=4,
                storage_key=f"{uuid4().hex}/content", sha256="b" * 64,
            ) if file else None,
            extracted_text="manual",
        )
        return source.id

    def add_knowledge(
        self, source: UUID, claim: str = "Replace the bearing seal.", *, context: dict | None = None,
    ) -> UUID:
        stored = KnowledgeRecord(
            subject="Pump P-101", claim=claim, type="rule", embedding=list(QUERY_VECTOR),
            context=context if context is not None else {"equipment": "P-101"},
        )
        self.call(self.manager.chunks.add_records, self.kb_id, source, [stored])
        return stored.id

    def search(self, **body):
        return self.api.post(self.path, headers=self.worker, json={"query": "bearing seal", **body})

    def test_results_carry_scores_citation_and_evidence_but_no_internal_fields(self) -> None:
        source = self.add_source(file=True)
        chunk = self.add_knowledge(source)
        response = self.search()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            (body["query"], body["mode"], body["embedding_model"]),
            ("bearing seal", "hybrid", "model-a"),
        )
        (result,) = body["results"]
        self.assertEqual(
            (result["rank"], result["chunk_id"], result["citation"], result["kind"], result["status"]),
            (1, str(chunk), f"[K:{chunk}]", "knowledge", "draft"),
        )
        self.assertEqual(set(result["scores"]), {"fused", "dense", "keyword"})
        self.assertIsNotNone(result["scores"]["fused"])
        (evidence,) = result["evidence"]
        self.assertEqual(evidence["source_id"], str(source))
        self.assertEqual(evidence["source_title"], "manual.txt")
        self.assertEqual(
            evidence["original_url"],
            f"/v1/knowledge-management/{self.kb}/sources/upload/{source}/original",
        )
        self.assertIsInstance(evidence["references"], list)
        self.assertEqual(set(result), {
            "rank", "chunk_id", "citation", "kind", "status", "title", "content", "scores", "evidence",
            "applicability",
        })
        self.assertEqual(set(evidence), {
            "source_id", "source_title", "source_kind", "references", "original_url",
        })
        self.assertNotIn("search_text", response.text)
        self.assertIsNone(result["applicability"])

    def test_applicability_copies_only_recorded_public_context(self) -> None:
        """Project short applicability strings without returning arbitrary context."""

        self.add_knowledge(self.add_source(), context={
            "applies_to": "  Pump P-101  ",
            "condition": "x" * 250,
            "private_note": "do not include",
        })
        response = self.search()
        self.assertEqual(response.status_code, 200, response.text)
        (result,) = response.json()["results"]
        self.assertEqual(result["applicability"], {"applies_to": "Pump P-101", "condition": "x" * 240})
        self.assertNotIn("private_note", response.text)

    def test_a_text_source_has_no_original_url(self) -> None:
        self.add_knowledge(self.add_source())
        (result,) = self.search().json()["results"]
        self.assertIsNone(result["evidence"][0]["original_url"])

    def test_statuses_and_context_filter_results(self) -> None:
        source = self.add_source()
        chunk = self.add_knowledge(source)
        self.assertEqual(len(self.search(statuses=["draft"]).json()["results"]), 1)
        self.assertEqual(self.search(statuses=["approved"]).json()["results"], [])
        self.assertEqual(self.search(kinds=["document_passage"]).json()["results"], [])
        scoped = self.search(context={"equipment": "P-101"}).json()["results"]
        self.assertEqual([item["chunk_id"] for item in scoped], [str(chunk)])
        self.assertEqual(self.search(context={"equipment": "P-202"}).json()["results"], [])

    def test_invalid_requests_are_rejected_with_422(self) -> None:
        for body in (
            {"statuses": ["deprecated"]},
            {"statuses": []},
            {"kinds": ["note"]},
            {"mode": "fuzzy"},
            {"query": "   "},
            {"query": "x" * 2001},
            {"limit": 0},
            {"limit": 51},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.search(**body).status_code, 422)

    def test_a_request_needs_a_token_and_a_known_knowledge_base(self) -> None:
        self.assertEqual(self.api.post(self.path, json={"query": "seal"}).status_code, 401)
        missing = self.api.post(
            "/v1/knowledge-management/unknown_kb/search", headers=self.worker, json={"query": "seal"}
        )
        self.assertEqual(missing.status_code, 404)

    def test_keyword_works_without_the_embedding_provider_and_vector_modes_return_503(self) -> None:
        chunk = self.add_knowledge(self.add_source())
        self.build.side_effect = ValueError("EMBEDDING_API_KEY is not set")
        keyword = self.search(mode="keyword")
        self.assertEqual(keyword.status_code, 200)
        self.assertEqual([item["chunk_id"] for item in keyword.json()["results"]], [str(chunk)])
        self.assertIsNone(keyword.json()["embedding_model"])
        for mode in ("hybrid", "dense"):
            with self.subTest(mode=mode):
                self.assertEqual(self.search(mode=mode).status_code, 503)

    def test_an_unreachable_embedding_provider_returns_503(self) -> None:
        self.add_knowledge(self.add_source())

        class Broken(FakeEmbeddings):
            async def aembed_query(self, text: str) -> list[float]:
                raise ConnectionError("provider down")

        self.build.return_value = Broken()
        self.assertEqual(self.search(mode="hybrid").status_code, 503)
        self.assertEqual(self.search(mode="keyword").status_code, 200)

    def test_a_model_mismatch_returns_409_naming_both_models(self) -> None:
        source = self.add_source()
        self.add_knowledge(source)
        runs = ExtractionRunRepository(self.manager.sessions, self.kb_id)
        run = self.call(
            runs.start, source_id=source, content="c" * 64, mode="knowledge", fingerprint="f",
            settings={"embedding_model": "model-z", "embedding_dim": 3},
        )
        self.call(runs.finish, run.id, "done")
        for mode in ("hybrid", "dense"):
            with self.subTest(mode=mode):
                response = self.search(mode=mode)
                self.assertEqual(response.status_code, 409)
                self.assertIn("model-z", response.json()["detail"])
                self.assertIn("model-a", response.json()["detail"])
        self.assertEqual(self.search(mode="keyword").status_code, 200)

    def test_removed_sources_leave_no_results(self) -> None:
        source = self.add_source()
        self.add_knowledge(source)
        self.assertEqual(len(self.search().json()["results"]), 1)
        removed = self.api.delete(
            f"/v1/knowledge-management/{self.kb}/sources/document/{source}", headers=self.expert
        )
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(self.search().json()["results"], [])

    def test_saved_source_opens_after_library_removal_until_kb_source_is_removed(self) -> None:
        """Distinguish file-library deletion from removing published KB evidence."""
        manual = PDF
        upload = self.api.post(
            "/v1/files", headers=self.expert,
            files={"file": ("manual.pdf", manual, "application/pdf")},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        file_id = upload.json()["id"]
        attached = self.api.post(
            f"/v1/documents/{self.kb}/files", headers=self.expert,
            json={"file_id": file_id},
        )
        self.assertEqual(attached.status_code, 201, attached.text)
        source_id = UUID(attached.json()["id"])
        self.add_knowledge(source_id)
        hit = self.search().json()["results"][0]
        original = hit["evidence"][0]["original_url"]
        self.assertEqual(self.api.get(original).status_code, 401)
        self.assertEqual(self.api.get(original, headers=self.worker).content, manual)

        removed_file = self.api.delete(f"/v1/files/{file_id}", headers=self.expert)
        self.assertEqual(removed_file.status_code, 204, removed_file.text)
        self.assertEqual(self.search().json()["results"], [])
        still_published = self.api.get(original, headers=self.worker)
        self.assertEqual(still_published.status_code, 200)
        self.assertEqual(still_published.content, manual)

        removed_source = self.api.delete(
            f"/v1/knowledge-management/{self.kb}/sources/upload/{source_id}",
            headers=self.expert,
        )
        self.assertEqual(removed_source.status_code, 204, removed_source.text)
        self.assertEqual(self.api.get(original, headers=self.worker).status_code, 404)


if __name__ == "__main__":
    unittest.main()

"""Retrieval on the application database: search filters, embedding guards, citations.

The integration tests need PostgreSQL + pgvector; see `test_database_migrations.py`:
    TEST_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent \
        uv run python -m unittest backend.tests.test_chunk_retrieval -v

The migrated databases use `EMBEDDING_DIM=3`; query embeddings are 3-dimensional too.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest import mock
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage
from sqlalchemy import insert, update

from backend.knowledge_bases import Retrieval, get_kb
from backend.src.agents.agent.service import (
    DocumentCitation,
    InvalidKnowledgeCitation,
    KnowledgeAgentService,
)
from backend.src.agents.rag.chunking import Chunk
from backend.src.agents.rag.store import SearchMode
from backend.src.services.application_source_pipeline import ApplicationSourcePipeline
from backend.src.services.job_worker import StageWorker
from backend.src.services.source_pipeline import JobFailed
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage import KnowledgeRecord, KnowledgeStatus, StorageError
from backend.src.storage.chunk_retrieval import ChunkRetrieval
from backend.src.storage.embeddings import (
    EmbeddingMismatch,
    check_embedding_dimension,
    check_embedding_model,
    verify_embedding_dimension,
)
from backend.src.storage.orm import (
    File,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeChunkSource,
    KnowledgeSource,
    SourceKind,
)
from backend.src.storage.repositories import (
    ChunkRepository,
    ExtractionRunRepository,
    NewFile,
    SourceRepository,
)
from backend.src.storage.repositories.ingestion_jobs import IngestionJobRepository, Job
from backend.src.storage.repositories.knowledge_bases import KnowledgeBaseRepository
from backend.tests.test_app_knowledge_repositories import RepositoryTest
from backend.tests.test_database_migrations import DATABASE_URL
from backend.tests.test_knowledge_agent import ScriptedChatModel


QUERY_VECTOR = [1.0, 0.0, 0.0]
OTHER_VECTOR = [0.0, 1.0, 0.0]


class FakeEmbeddings:
    """Embeds every query as the same vector."""

    async def aembed_query(self, text: str) -> list[float]:
        return list(QUERY_VECTOR)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(OTHER_VECTOR) for _ in texts]


class EmbeddingGuardTest(unittest.TestCase):
    def test_a_different_dimension_names_both_sizes_and_the_fix(self) -> None:
        with self.assertRaises(EmbeddingMismatch) as caught:
            check_embedding_dimension(stored=1024, configured=768)
        message = str(caught.exception)
        self.assertIn("1024", message)
        self.assertIn("768", message)
        self.assertIn("re-embed", message.lower())
        self.assertIsInstance(caught.exception, StorageError)
        check_embedding_dimension(stored=768, configured=768)

    def test_a_missing_column_size_is_reported(self) -> None:
        with self.assertRaises(EmbeddingMismatch):
            check_embedding_dimension(stored=None, configured=768)

    def test_a_different_model_names_the_knowledge_base_and_both_models(self) -> None:
        with self.assertRaises(EmbeddingMismatch) as caught:
            check_embedding_model("line_a", configured="model-b", recorded={"model-a"})
        message = str(caught.exception)
        for part in ("line_a", "model-a", "model-b", "re-embed"):
            self.assertIn(part, message.lower() if part == "re-embed" else message)

    def test_matching_unknown_or_unconfigured_models_pass(self) -> None:
        check_embedding_model("kb", configured="model-a", recorded={"model-a"})
        check_embedding_model("kb", configured="model-a", recorded=set())
        check_embedding_model("kb", configured=None, recorded={"model-a"})


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class RetrievalTest(RepositoryTest):
    """A knowledge base with chunks to search."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.chunks = ChunkRepository(self.sessions)
        self.sources = SourceRepository(self.sessions)
        self.runs = ExtractionRunRepository(self.sessions, self.kb.id)
        self.retrieval = self.retrieval_for(self.kb)

    def retrieval_for(self, kb: KnowledgeBase, model: str | None = "model-a") -> ChunkRetrieval:
        return ChunkRetrieval(
            kb.id,
            kb.name,
            self.chunks,
            self.sources,
            ExtractionRunRepository(self.sessions, kb.id),
            FakeEmbeddings(),
            vector_size=3,
            embedding_model=model,
        )

    async def add_file_source(self, kb: KnowledgeBase | None = None) -> tuple[UUID, UUID]:
        source = await self.sources.create(
            (kb or self.kb).id,
            kind=SourceKind.DOCUMENT,
            title="manual.txt",
            submitted_by=self.user.id,
            file=NewFile(
                filename="manual.txt", content_type="text/plain", size_bytes=4,
                storage_key=f"{uuid4().hex}/content", sha256="b" * 64,
            ),
            extracted_text="manual",
        )
        return source.id, source.file.id

    async def add_knowledge(
        self, source: UUID, *, claim: str = "Replace the bearing seal.",
        embedding: list[float] | None = None, kb: KnowledgeBase | None = None, **values,
    ) -> UUID:
        stored = KnowledgeRecord(
            subject="Pump P-101", claim=claim, type=KnowledgeType.RULE,
            embedding=embedding or list(QUERY_VECTOR), **values,
        )
        await self.chunks.add_records((kb or self.kb).id, source, [stored])
        return stored.id

    async def add_passage(
        self, source: UUID, *, text_value: str = "Replace the bearing seal.",
        embedding: list[float] | None = None, kb: KnowledgeBase | None = None,
    ) -> UUID:
        (chunk_id,) = await self.chunks.add_passages(
            (kb or self.kb).id,
            source,
            [Chunk(doc_id=str(source), index=0, text=text_value, title="Manual")],
            [embedding or list(QUERY_VECTOR)],
        )
        return chunk_id

    def change(self, model, row_id: UUID, **values) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(model).where(model.id == row_id).values(**values))

    def soft_delete(self, model, row_id: UUID) -> None:
        self.change(model, row_id, deleted_at=datetime.now(timezone.utc))

    async def found_knowledge(self, retrieval: ChunkRetrieval | None = None) -> set[UUID]:
        hits = await (retrieval or self.retrieval).search_knowledge("bearing seal", k=20)
        return {hit.record.id for hit in hits}

    async def found_passages(
        self, mode: SearchMode, retrieval: ChunkRetrieval | None = None
    ) -> set[str]:
        hits = await (retrieval or self.retrieval).search_documents("bearing seal", mode=mode, k=20)
        return {hit.chunk_id for hit in hits}


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class KnowledgeSearchTest(RetrievalTest):
    async def test_a_draft_is_returned_with_its_status_and_source(self) -> None:
        source = await self.add_source()
        chunk = await self.add_knowledge(source)
        (hit,) = await self.retrieval.search_knowledge("bearing seal")
        self.assertEqual(
            (hit.record.id, hit.record.status, hit.record.evidence_id, hit.confident),
            (chunk, KnowledgeStatus.DRAFT, source, True),
        )
        self.assertEqual((hit.vector_rank, hit.lexical_rank), (1, 1))
        self.assertAlmostEqual(hit.vector_similarity, 1.0, places=5)

    async def test_deprecated_deleted_and_foreign_chunks_are_never_returned(self) -> None:
        source = await self.add_source()
        kept = await self.add_knowledge(source)
        deprecated = await self.add_knowledge(source)
        deleted = await self.add_knowledge(source)
        self.change(KnowledgeChunk, deprecated, status="deprecated")
        self.soft_delete(KnowledgeChunk, deleted)
        other_kb = self.add_kb()
        await self.add_knowledge(await self.add_source(other_kb), kb=other_kb)
        self.assertEqual(await self.found_knowledge(), {kept})

    async def test_chunks_of_deleted_sources_files_and_knowledge_bases_are_never_returned(self) -> None:
        kept_source = await self.add_source()
        kept = await self.add_knowledge(kept_source)
        gone_source = await self.add_source()
        await self.add_knowledge(gone_source)
        self.soft_delete(KnowledgeSource, gone_source)
        file_source, file_id = await self.add_file_source()
        await self.add_knowledge(file_source)
        self.soft_delete(File, file_id)
        self.assertEqual(await self.found_knowledge(), {kept})

        doomed = self.add_kb()
        await self.add_knowledge(await self.add_source(doomed), kb=doomed)
        doomed_retrieval = self.retrieval_for(doomed)
        self.assertEqual(len(await doomed_retrieval.search_knowledge("bearing seal")), 1)
        await KnowledgeBaseRepository(self.sessions).delete(doomed.name)
        self.assertEqual(await doomed_retrieval.search_knowledge("bearing seal"), [])

    async def test_a_chunk_stays_searchable_while_one_of_its_sources_is_live(self) -> None:
        live, gone = await self.add_source(), await self.add_source()
        chunk = await self.add_knowledge(live)
        self.link_source(chunk, gone)
        await self.sources.remove(self.kb.id, gone, actor_id=self.user.id, role=self.user.role)
        self.assertEqual(await self.found_knowledge(), {chunk})
        await self.sources.remove(self.kb.id, live, actor_id=self.user.id, role=self.user.role)
        self.assertEqual(await self.found_knowledge(), set())

    def link_source(self, chunk: UUID, source: UUID) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(KnowledgeChunkSource).values(
                chunk_id=chunk, source_id=source, references=[{"type": "source"}]
            ))

    async def test_passages_are_not_knowledge_and_the_filter_grammar_still_applies(self) -> None:
        source = await self.add_source()
        await self.add_passage(source)
        scoped = await self.add_knowledge(source, context={"equipment": "P-101"})
        await self.add_knowledge(source, context={"equipment": "P-202"})
        found = await self.retrieval.search_knowledge(
            "bearing seal", filters={"context.equipment": {"$eq": "P-101"}}, k=20
        )
        self.assertEqual([hit.record.id for hit in found], [scoped])

    async def test_a_chunk_without_an_embedding_is_still_found_by_keyword(self) -> None:
        source = await self.add_source()
        chunk = await self.add_knowledge(source)
        self.change(KnowledgeChunk, chunk, embedding=None)
        (hit,) = await self.retrieval.search_knowledge("bearing seal")
        self.assertEqual((hit.record.id, hit.vector_rank), (chunk, None))
        self.assertEqual(hit.lexical_rank, 1)

    async def test_the_nearest_vector_hit_is_low_confidence_below_the_threshold(self) -> None:
        source = await self.add_source()
        await self.add_knowledge(source, claim="Unrelated wording.", embedding=OTHER_VECTOR)
        (hit,) = await self.retrieval.search_knowledge("zebra", min_vector_similarity=0.3)
        self.assertFalse(hit.confident)

    async def test_the_search_fails_when_the_knowledge_base_was_embedded_with_another_model(self) -> None:
        source = await self.add_source()
        await self.add_knowledge(source)
        run = await self.runs.start(
            source_id=source, content="c" * 64, mode="knowledge", fingerprint="f",
            settings={"embedding_model": "model-a", "embedding_dim": 3},
        )
        await self.runs.finish(run.id, "done")
        self.assertEqual(len(await self.retrieval.search_knowledge("bearing seal")), 1)
        with self.assertRaises(EmbeddingMismatch) as caught:
            await self.retrieval_for(self.kb, model="model-b").search_knowledge("bearing seal")
        self.assertIn("model-a", str(caught.exception))


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class PassageSearchTest(RetrievalTest):
    async def test_every_mode_excludes_deprecated_deleted_foreign_and_unsourced_passages(self) -> None:
        live, gone = await self.add_source(), await self.add_source()
        kept = await self.add_passage(live)
        deprecated = await self.add_passage(live)
        deleted = await self.add_passage(live)
        orphaned = await self.add_passage(gone)
        self.change(KnowledgeChunk, deprecated, status="deprecated")
        self.soft_delete(KnowledgeChunk, deleted)
        self.soft_delete(KnowledgeSource, gone)
        other_kb = self.add_kb()
        await self.add_passage(await self.add_source(other_kb), kb=other_kb)
        await self.add_knowledge(live)
        for mode in SearchMode:
            with self.subTest(mode=mode):
                found = await self.found_passages(mode)
                self.assertEqual(found, {str(kept)})
                self.assertNotIn(str(orphaned), found)

    async def test_passages_of_a_deleted_file_or_knowledge_base_are_excluded(self) -> None:
        source, file_id = await self.add_file_source()
        await self.add_passage(source)
        self.assertEqual(len(await self.found_passages(SearchMode.HYBRID)), 1)
        self.soft_delete(File, file_id)
        self.assertEqual(await self.found_passages(SearchMode.HYBRID), set())

        doomed = self.add_kb()
        await self.add_passage(await self.add_source(doomed), kb=doomed)
        retrieval = self.retrieval_for(doomed)
        self.soft_delete(KnowledgeBase, doomed.id)
        for mode in SearchMode:
            self.assertEqual(await self.found_passages(mode, retrieval), set())

    async def test_a_draft_passage_is_returned_with_its_status_and_document(self) -> None:
        source = await self.add_source()
        chunk = await self.add_passage(source)
        (hit,) = await self.retrieval.search_documents("bearing seal", mode=SearchMode.HYBRID)
        self.assertEqual(
            (hit.chunk_id, hit.doc_id, hit.chunk_index, hit.title, hit.metadata["review_status"]),
            (str(chunk), str(source), 0, "Manual", "draft"),
        )
        self.assertEqual((hit.vector_rank, hit.lexical_rank), (1, 1))

    async def test_a_document_is_returned_once_by_its_best_passage(self) -> None:
        source = await self.add_source()
        await self.chunks.add_passages(
            self.kb.id, source,
            [Chunk(doc_id=str(source), index=i, text="bearing seal", title="Manual") for i in range(3)],
            [list(QUERY_VECTOR)] * 3,
        )
        hits = await self.retrieval.search_documents("bearing seal", mode=SearchMode.HYBRID, k=5)
        self.assertEqual([hit.doc_id for hit in hits], [str(source)])

    async def test_get_document_reads_live_passages_in_order(self) -> None:
        source = await self.add_source()
        _first, second = await self.chunks.add_passages(
            self.kb.id, source,
            [Chunk(doc_id=str(source), index=i, text=f"part {i}", title="Manual") for i in range(2)],
            [list(QUERY_VECTOR)] * 2,
        )
        self.assertEqual(
            [hit.text for hit in await self.retrieval.get_document(str(source))], ["part 0", "part 1"]
        )
        self.soft_delete(KnowledgeChunk, second)
        self.assertEqual(
            [hit.text for hit in await self.retrieval.get_document(str(source))], ["part 0"]
        )
        self.soft_delete(KnowledgeSource, source)
        self.assertEqual(await self.retrieval.get_document(str(source)), [])
        self.assertEqual(await self.retrieval.get_document("not-a-document"), [])

    async def test_search_fails_when_passages_were_embedded_with_another_model(self) -> None:
        source = await self.add_source()
        await self.add_passage(source)
        run = await self.runs.start(
            source_id=source, content="d" * 64, mode="documents", fingerprint="g",
            settings={"embedding_model": "model-a", "embedding_dim": 3},
        )
        await self.runs.finish(run.id, "done")
        with self.assertRaises(EmbeddingMismatch):
            await self.retrieval_for(self.kb, model="model-b").search_documents("bearing seal")


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class EvidenceAndScopeTest(RetrievalTest):
    async def test_get_conversation_returns_the_live_source_text(self) -> None:
        source = await self.add_source(text_value="Expert: replace the seal.")
        conversation = await self.retrieval.get_conversation(source)
        self.assertEqual(
            (conversation.id, conversation.transcript), (source, "Expert: replace the seal.")
        )
        self.assertIsNone(await self.retrieval.get_conversation(uuid4()))
        self.assertIsNone(await self.retrieval_for(self.add_kb()).get_conversation(source))
        self.soft_delete(KnowledgeSource, source)
        self.assertIsNone(await self.retrieval.get_conversation(source))

    async def test_get_knowledge_skips_unknown_and_deleted_chunks(self) -> None:
        source = await self.add_source()
        kept, deleted = await self.add_knowledge(source), await self.add_knowledge(source)
        self.soft_delete(KnowledgeChunk, deleted)
        found = await self.retrieval.get_knowledge([uuid4(), deleted, kept])
        self.assertEqual([record.id for record in found], [kept])

    async def test_scope_edits_replace_the_search_text_and_embedding_together(self) -> None:
        source = await self.add_source()
        chunk = await self.add_knowledge(source, context={"equipment": "P-101"})
        (loaded,) = await self.chunks.get_records(self.kb.id, [chunk])
        loaded.context = {"equipment": "P-202"}
        loaded.embedding = list(OTHER_VECTOR)
        updated = await self.chunks.update_scope(self.kb.id, [loaded], leading_keys=("equipment",))
        self.assertEqual(updated, 1)
        (stored,) = await self.chunks.get_records(self.kb.id, [chunk], embeddings=True)
        self.assertEqual((stored.context, stored.embedding), ({"equipment": "P-202"}, OTHER_VECTOR))
        (hit,) = await self.retrieval.search_knowledge("P-202")
        self.assertEqual(hit.record.id, chunk)
        self.assertEqual(await self.chunks.update_scope(self.add_kb().id, [loaded]), 0)


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class StartupCheckTest(RetrievalTest):
    async def test_the_stored_dimension_is_compared_with_embedding_dim(self) -> None:
        with mock.patch.dict(os.environ, {"EMBEDDING_DIM": "3"}):
            await verify_embedding_dimension(self.sessions)
        with mock.patch.dict(os.environ, {"EMBEDDING_DIM": "1024"}):
            with self.assertRaises(EmbeddingMismatch) as caught:
                await verify_embedding_dimension(self.sessions)
        self.assertIn("1024", str(caught.exception))
        self.assertIn("3", str(caught.exception))


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class CitationTest(RetrievalTest):
    def agent(self, *responses: AIMessage, retrieval: Retrieval = Retrieval.KNOWLEDGE) -> KnowledgeAgentService:
        return KnowledgeAgentService(
            store=self.retrieval,
            model=ScriptedChatModel(responses=list(responses)),
            system_prompt="Answer from the knowledge base.",
            timeout_seconds=10,
            retrieval=retrieval,
        )

    @staticmethod
    def search(tool: str) -> AIMessage:
        return AIMessage(
            content="", tool_calls=[{"name": tool, "args": {"query": "bearing seal"}, "id": "c1"}]
        )

    async def test_citations_to_retrieved_chunks_validate_and_others_are_rejected(self) -> None:
        source = await self.add_source()
        chunk = await self.add_knowledge(source)
        answer = await self.agent(
            self.search("search_knowledge"), AIMessage(content=f"Replace the seal [K:{chunk}].")
        ).ask("How do I fix the pump?")
        self.assertEqual([UUID(citation.knowledge_id) for citation in answer.citations], [chunk])
        self.assertEqual(UUID(answer.citations[0].evidence_id), source)

        with self.assertRaises(InvalidKnowledgeCitation):
            await self.agent(
                self.search("search_knowledge"),
                AIMessage(content=f"Replace the seal [K:{uuid4()}]."),
            ).ask("How do I fix the pump?")

    async def test_document_citations_use_the_passage_document_id(self) -> None:
        source = await self.add_source()
        await self.add_passage(source)
        answer = await self.agent(
            self.search("search_documents"),
            AIMessage(content=f"See the manual [D:{source}]."),
            retrieval=Retrieval.DOCUMENTS,
        ).ask("How do I fix the pump?")
        self.assertEqual(answer.citations, (DocumentCitation(doc_id=str(source), title="Manual"),))


class FakeCatalog:
    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb

    async def refresh(self) -> None:
        """Keep the fixture knowledge base active."""

    def resolve_id(self, knowledge_base_id: UUID):
        return (self.kb.name, get_kb("general")) if knowledge_base_id == self.kb.id else None


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class IngestionEmbeddingRecordTest(RetrievalTest):
    async def job(self, source: UUID) -> Job:
        """Create and lease a document-extraction job through the real queue."""

        queue = IngestionJobRepository(self.sessions)
        await queue.enqueue(self.kb.id, source, "extract", mode="documents")
        return await queue.claim("extract", "test-worker")

    async def extract(self, source: UUID) -> None:
        """Run the worker's prepared-write and completion path."""

        worker = StageWorker(
            IngestionJobRepository(self.sessions), self.pipeline(), ["extract"], name="test-worker",
        )
        await worker.process(await self.job(source))

    def pipeline(self) -> ApplicationSourcePipeline:
        return ApplicationSourcePipeline(
            FakeCatalog(self.kb), self.sources, self.chunks, None, self.sessions,
            embeddings_factory=lambda **_: FakeEmbeddings(), vector_size=3,
        )

    async def test_a_run_records_the_embedding_model_and_dimension(self) -> None:
        source = await self.add_source(text_value="Replace the bearing seal.")
        with mock.patch.dict(os.environ, {"EMBEDDING_MODEL": "model-a", "EMBEDDING_DIM": "3"}):
            await self.extract(source)
        (run,) = await self.runs.for_source(source)
        self.assertEqual(run.settings["embedding_model"], "model-a")
        self.assertEqual(run.settings["embedding_dim"], 3)
        self.assertEqual(run.status, "done")
        self.assertTrue(await self.chunks.has_results(self.kb.id, source, "documents"))

    async def test_ingestion_refuses_a_model_that_differs_from_earlier_runs(self) -> None:
        first = await self.add_source(text_value="Replace the bearing seal.")
        with mock.patch.dict(os.environ, {"EMBEDDING_MODEL": "model-a", "EMBEDDING_DIM": "3"}):
            await self.extract(first)
        second = await self.add_source(text_value="Grease the shaft every month.")
        with mock.patch.dict(os.environ, {"EMBEDDING_MODEL": "model-b", "EMBEDDING_DIM": "3"}):
            with self.assertRaises(JobFailed) as caught:
                await self.pipeline().run_extract(await self.job(second))
        self.assertIn("model-a", str(caught.exception))
        self.assertEqual(await self.runs.for_source(second), [])


if __name__ == "__main__":
    unittest.main()

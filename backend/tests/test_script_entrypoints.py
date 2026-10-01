"""Unit tests for the standalone ingestion and retrieval CLI interfaces."""

from __future__ import annotations

import tempfile
import unittest
import io
from pathlib import Path
from unittest import mock

from backend.knowledge_bases import get_kb
from backend.scripts.kb import build_parser, ingest, read_transcripts, reset
from backend.src.services.knowledge_extraction import KnowledgeExtractionService
from backend.tests.test_knowledge_extraction_service import FakeLLM
from backend.tests.test_knowledge_ingestion_service import FakeStore


class IngestEntrypointTest(unittest.TestCase):
    def test_reads_one_text_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.txt"
            path.write_text("  Expert: Clean the sensor.  ", encoding="utf-8")

            (item,) = read_transcripts(path, stated_by="Expert A")

        self.assertEqual(item.transcript, "Expert: Clean the sensor.")
        self.assertEqual(item.stated_by, "Expert A")
        self.assertEqual(item.metadata, {"source_file": "conversation.txt"})

    def test_reads_csv_rows_and_preserves_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversations.csv"
            path.write_text(
                "case_id,stated_by,raw_conversation\n"
                '1,Expert A,"Expert: Clean the lens."\n',
                encoding="utf-8",
            )

            (item,) = read_transcripts(path)

        self.assertEqual(item.stated_by, "Expert A")
        self.assertEqual(item.metadata["case_id"], "1")
        self.assertEqual(item.metadata["source_row"], "2")

    def test_rejects_csv_without_transcript_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversations.csv"
            path.write_text("text\nhello\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "transcript column"):
                list(read_transcripts(path))

    def test_rejects_empty_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.txt"
            path.write_text("   ", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty"):
                list(read_transcripts(path))

    def test_ingest_parser_requires_only_a_knowledge_base(self) -> None:
        args = build_parser().parse_args(["ingest", "maintenance"])
        self.assertEqual(args.kb, "maintenance")
        self.assertIsNone(args.file)
        self.assertEqual(args.batch_size, 1)
        self.assertIsNone(args.concurrency)

    def test_ingest_parser_accepts_batch_options(self) -> None:
        args = build_parser().parse_args(
            ["ingest", "maintenance", "--batch-size", "8", "--concurrency", "4"]
        )
        self.assertEqual((args.batch_size, args.concurrency), (8, 4))
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["ingest", "maintenance", "--batch-size", "0"])

    def test_ingest_parser_accepts_a_file_override(self) -> None:
        args = build_parser().parse_args(
            ["ingest", "maintenance", "--file", "conversation.txt", "--limit", "3"]
        )
        self.assertEqual(args.file, Path("conversation.txt"))
        self.assertEqual(args.limit, 3)

    def test_ingest_parser_rejects_non_positive_limit(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["ingest", "maintenance", "--limit", "0"])


class QueryEntrypointTest(unittest.TestCase):
    def test_query_parser_takes_knowledge_base_and_question(self) -> None:
        args = build_parser().parse_args(["query", "maintenance", "How do I clean M01?"])
        self.assertEqual(args.kb, "maintenance")
        self.assertEqual(args.question, "How do I clean M01?")

    def test_reset_requires_explicit_confirmation_flag(self) -> None:
        args = build_parser().parse_args(["reset", "maintenance"])
        self.assertFalse(args.yes)


class IngestionCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_command_saves_active_extraction_version_and_prompt(self) -> None:
        spec = get_kb("enterprise_rag")
        extractor = KnowledgeExtractionService(FakeLLM('{"knowledge_items":[]}'), spec.extract_prompt)
        store = FakeStore()
        store.processed_metadata_values = mock.AsyncMock(return_value=set())
        store.aclose = mock.AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.txt"
            path.write_text("A document to extract.")
            args = build_parser().parse_args(["ingest", "enterprise_rag", "--file", str(path)])
            with (
                mock.patch("backend.scripts.kb.KnowledgeExtractionService.from_env", return_value=extractor),
                mock.patch("backend.scripts.kb.KnowledgeStore.for_kb", new_callable=mock.AsyncMock, return_value=store),
                mock.patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(await ingest(spec, args), 0)
        conversation, = store.conversations.values()
        self.assertEqual(conversation.metadata["extraction"], {
            "version": "complete-units-v2", **extractor.provenance,
        })
        store.aclose.assert_awaited_once()

    async def test_reset_requires_yes_and_targets_only_enterprise_database(self) -> None:
        with (
            mock.patch("backend.scripts.kb.drop_database", new_callable=mock.AsyncMock, return_value=True) as drop,
            mock.patch("backend.scripts.kb.server_url_from_env", return_value="server"),
            mock.patch("sys.stdout", new_callable=io.StringIO),
            mock.patch("sys.stderr", new_callable=io.StringIO),
        ):
            self.assertEqual(await reset(get_kb("enterprise_rag"), confirmed=False), 2)
            drop.assert_not_awaited()
            self.assertEqual(await reset(get_kb("enterprise_rag"), confirmed=True), 0)
            drop.assert_awaited_once_with("server", "kb_enterprise_rag")


if __name__ == "__main__":
    unittest.main()

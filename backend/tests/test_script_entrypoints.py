"""Unit tests for the standalone ingestion and retrieval CLI interfaces."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.scripts.ingest_knowledge import build_parser as build_ingest_parser
from backend.scripts.ingest_knowledge import read_transcripts
from backend.scripts.query_knowledge import build_parser as build_query_parser


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

    def test_ingest_parser_requires_only_a_file(self) -> None:
        args = build_ingest_parser().parse_args(["conversation.txt"])
        self.assertEqual(args.file, Path("conversation.txt"))


class QueryEntrypointTest(unittest.TestCase):
    def test_query_parser_requires_only_the_question(self) -> None:
        args = build_query_parser().parse_args(["How do I clean M01?"])
        self.assertEqual(args.query, "How do I clean M01?")


if __name__ == "__main__":
    unittest.main()

"""Redwood Inference internal documents (EnterpriseRAG-Bench, gold subset)."""

from pathlib import Path

from backend.knowledge_bases.base import (
    DATA_DIR,
    ContextFilter,
    DatasetSpec,
    KnowledgeBaseSpec,
)


# Written by `python -m backend.knowledge_bases.enterprise_rag.prepare`.
CORPUS_FILE = DATA_DIR / "enterprise_rag" / "corpus.csv"
QUESTIONS_FILE = DATA_DIR / "enterprise_rag" / "questions.jsonl"
SOURCE_TYPES = (
    "confluence",
    "fireflies",
    "github",
    "gmail",
    "google_drive",
    "hubspot",
    "jira",
    "linear",
    "slack",
)

SPEC = KnowledgeBaseSpec.from_directory(
    Path(__file__).parent,
    description=__doc__,
    extraction_version="complete-units-v2",
    dataset=DatasetSpec(
        CORPUS_FILE,
        transcript_column="document",
        id_column="doc_id",
        context_columns=("source_type", "title"),
    ),
    context_filters=(
        ContextFilter(
            "source_type",
            "Only search knowledge from one source system: "
            + ", ".join(SOURCE_TYPES)
            + " (fireflies = meeting transcripts). Use only when the user names the source.",
            include_unscoped=False,
        ),
    ),
    embedding_context_keys=("customer", "project", "system", "region", "ticket", "title"),
)

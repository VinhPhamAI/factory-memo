"""Factory maintenance conversations between junior engineers and experts."""

from pathlib import Path

from backend.knowledge_bases.base import (
    DATA_DIR,
    ContextFilter,
    DatasetSpec,
    KnowledgeBaseSpec,
    Retrieval,
    RetrievalQuestions,
)
from backend.src.agents.rag.chunking import ChunkingConfig


DATA = DATA_DIR / "maintainance"

SPEC = KnowledgeBaseSpec.from_directory(
    Path(__file__).parent,
    description=__doc__,
    dataset=DatasetSpec(
        DATA / "expert_knowledge_extraction_benchmark_20_conversations_en.csv",
        id_column="case_id",
    ),
    # Conversations are searched directly: each is short (44-172 words), so
    # one chunk per conversation. Set Retrieval.KNOWLEDGE to search records
    # extracted by extract.md instead (answered with agent.md).
    retrieval=Retrieval.DOCUMENTS,
    chunking=ChunkingConfig(),
    retrieval_questions=RetrievalQuestions(
        DATA / "retrieval_evaluation_questions_answers.csv",
        gold_column="gold_case_ids",
    ),
    # Knowledge retrieval only.
    context_filters=(
        ContextFilter(
            "machine_id",
            "Machine ID the user names, e.g. M01. Records that name no machine "
            "still match because they apply to every machine.",
        ),
        ContextFilter(
            "machine_model",
            "Machine model the user names, e.g. X200. Records that name no model "
            "still match because they apply generally.",
        ),
    ),
    embedding_context_keys=("machine_id", "machine_model", "component_model", "condition"),
)

"""Factory maintenance conversations between junior engineers and experts."""

from pathlib import Path

from backend.knowledge_bases.base import (
    DATA_DIR,
    ContextFilter,
    DatasetSpec,
    KnowledgeBaseSpec,
)


SPEC = KnowledgeBaseSpec.from_directory(
    Path(__file__).parent,
    description=__doc__,
    dataset=DatasetSpec(
        DATA_DIR / "expert_knowledge_extraction_benchmark_20_conversations_en.csv"
    ),
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

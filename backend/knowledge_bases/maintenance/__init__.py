"""Factory maintenance conversations between junior engineers and experts."""

from pathlib import Path

from backend.knowledge_bases.base import DATA_DIR, DatasetSpec, KnowledgeBaseSpec


SPEC = KnowledgeBaseSpec.from_directory(
    Path(__file__).parent,
    description=__doc__,
    dataset=DatasetSpec(
        DATA_DIR / "expert_knowledge_extraction_benchmark_20_conversations_en.csv"
    ),
)

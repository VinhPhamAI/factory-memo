"""Expert conversations and equipment manuals of the benchmark QA dataset.

The extraction contract lives in this package. Benchmark runs use their own
database and never depend on, or write to, the maintenance knowledge base.
Manual chunks are indexed as documents in the same database.
"""

from pathlib import Path

from backend.knowledge_bases.base import ContextFilter, KnowledgeBaseSpec
from backend.knowledge_bases.industrial_qa.schema import IndustrialQaExtraction


SPEC = KnowledgeBaseSpec.from_directory(
    Path(__file__).parent,
    name="industrial_qa",
    description=__doc__,
    context_filters=(
        ContextFilter(
            "machine_id",
            "Machine or asset ID named in the question. Unscoped records still match.",
        ),
        ContextFilter(
            "machine_model",
            "Machine or equipment model named in the question. Unscoped records still match.",
        ),
    ),
    embedding_context_keys=("machine_id", "machine_model", "component_model", "condition"),
    extraction_version="industrial-qa-case-lesson-v1",
    extraction_output_model=IndustrialQaExtraction,
)

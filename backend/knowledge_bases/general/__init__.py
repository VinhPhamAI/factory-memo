"""General-purpose knowledge extracted from conversations on any topic."""

from pathlib import Path

from backend.knowledge_bases.base import KnowledgeBaseSpec, Retrieval
from backend.knowledge_bases.general.schema import GeneralExtraction


SPEC = KnowledgeBaseSpec.from_directory(
    Path(__file__).parent,
    description=__doc__,
    # No default dataset: ingest with `kb ingest general --file <path>`.
    retrieval=Retrieval.KNOWLEDGE,
    # Scope values are free text, so they lead the embedding instead of
    # becoming exact-match filters; subject, type and valid_on still filter.
    embedding_context_keys=("applies_to", "condition"),
    extraction_version="general-items-v2",
    extraction_output_model=GeneralExtraction,
)

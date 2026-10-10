"""Expert conversations and equipment manuals of the benchmark QA dataset.

The extraction contract lives in this package. Benchmark runs use their own
database and never depend on, or write to, the maintenance knowledge base.
Manual chunks are indexed as documents in the same database.
"""

from pathlib import Path

from backend.knowledge_bases.base import ONTOLOGY_FILE, ContextFilter, KnowledgeBaseSpec
from backend.knowledge_bases.industrial_qa.catalog import Catalog
from backend.knowledge_bases.industrial_qa.schema import IndustrialQaExtraction


DIRECTORY = Path(__file__).parent
# Canonical equipment names (catalog_types.yaml + catalog_assets.yaml).
CATALOG = Catalog.from_files()


SPEC = KnowledgeBaseSpec.from_directory(
    DIRECTORY,
    name="industrial_qa",
    description=__doc__,
    context_filters=(
        ContextFilter(
            "machine_id",
            "Machine or asset ID named in the question. Unscoped records still match.",
        ),
        ContextFilter(
            "machine_model",
            "Canonical equipment family: MR-JE, FX5 or i4L. Known asset IDs remain eligible even if family metadata disagrees.",
        ),
        ContextFilter("machine_model_specific", "Exact manufacturer model, e.g. MR-JE-70A.", include_unscoped=False),
    ),
    embedding_context_keys=("machine_id", "machine_model", "machine_model_specific", "component_model", "condition"),
    extraction_version="industrial-qa-case-lesson-v3",
    extraction_output_model=IndustrialQaExtraction,
    ontology=CATALOG.ontology(DIRECTORY / ONTOLOGY_FILE),
    record_normalizer=CATALOG.normalize_records,
    knowledge_filter_builder=CATALOG.knowledge_filter,
)

"""AssetOpsBench static reference data (IBM public sample corpus)."""

from pathlib import Path

from backend.knowledge_bases.base import DATA_DIR, ContextFilter, DatasetSpec, KnowledgeBaseSpec


OUTPUT_DIR = DATA_DIR / "assetopsbench"
CORPUS_FILE = OUTPUT_DIR / "corpus.csv"
QUESTIONS_FILE = OUTPUT_DIR / "questions.jsonl"

SPEC = KnowledgeBaseSpec.from_directory(
    Path(__file__).parent,
    description=__doc__,
    extraction_version="static-reference-v1",
    dataset=DatasetSpec(
        CORPUS_FILE,
        transcript_column="document",
        id_column="doc_id",
        context_columns=("source_type", "title", "asset_id", "asset_type", "site_id"),
    ),
    context_filters=(
        ContextFilter("asset_id", "Exact asset identifier in the source, e.g. Chiller 6, mp_1 or PUMP3. Use only when known."),
        ContextFilter("asset_type", "Exact source asset type, e.g. CHILLER, COMPRESSOR or pump. Do not guess capitalization or aliases."),
        ContextFilter("site_id", "Exact site identifier named by the user, e.g. MAIN or NORTH."),
        ContextFilter(
            "source_type",
            "Source kind: asset_profile, asset_catalog, sensor_catalog, failure_mode_catalog, "
            "failure_code, failure_modes, feature_catalog, model_catalog or work_order. "
            "Use only to narrow to a known source kind.",
            include_unscoped=False,
        ),
    ),
    embedding_context_keys=("asset_id", "asset_type", "site_id", "title", "source_type"),
)

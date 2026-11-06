# Knowledge bases

Run all commands below from the project root. For dependency installation,
PostgreSQL setup and API/UI startup, see the [project README](../../README.md).
Configure shared services using [`.env.example`](../../.env.example).

Each knowledge base (KB) pairs one dataset with its own extraction prompt, agent
prompt, and PostgreSQL database `kb_<name>` on the `DATABASE_URL` server. The
database is created on first use. The LLM, embedding model, and database server
stay in `.env`; everything domain-specific lives in `backend/knowledge_bases/<name>/`:

```
backend/knowledge_bases/
  __init__.py          # registry: KNOWLEDGE_BASES, get_kb()
  base.py              # KnowledgeBaseSpec, DatasetSpec, ContextFilter, Retrieval
  stores.py            # open_store(): the store a KB's agent searches
  general/
    __init__.py        # SPEC: typed extraction, knowledge retrieval
    schema.py          # extraction output schema
    prompts/
      extract.md         # extraction prompt; must contain {RAW_CONVERSATION}
      agent.md           # agent prompt for knowledge retrieval
      embedding_query.md # optional query-side embedding instruction
  industrial_qa/       # equipment catalog, expert knowledge and manual retrieval
```

To add a KB, copy `general/` to a new name, edit its prompts and schema, then
add its `SPEC` to `KNOWLEDGE_BASES` in `__init__.py`. Add a `DatasetSpec` if the
KB has a default ingestion dataset. See [general/README.md](general/README.md).
The built-in registry contains `general` and `industrial_qa`; the EnterpriseRAG
and AssetOpsBench integrations described below are absent from this checkout.

### Knowledge bases created in the UI

The administrator page **Kho kiến thức** (`/admin/knowledge-bases`, **Tạo kho kiến thức**) calls
`POST /v1/knowledge-bases` with `name` (id, same pattern as above), `title`,
optional `description` and `template` (only `general`). The new KB copies the
template's policy (`custom.py`: prompts, typed schema and retrieval settings) and
is recorded in the `knowledge_bases` table of `APP_DATABASE_URL`. No database is
created for the KB. The API loads those rows on startup and serves them for QA
and capture without a restart. `GET /v1/knowledge-bases` lists only these rows.

Creation is refused for a used or soft-deleted name, the reserved names
`general` and `industrial_qa`, or beyond `KB_MAX_CREATED` (default 50). Listing
requires any active account. Creation requires an expert or administrator.
There is no rename or delete endpoint; the `kb` CLI does not know created KBs.
Industrial QA is not offered as a template because it depends on its equipment
catalog, manuals and benchmark.

## Retrieval modes

`KnowledgeBaseSpec.retrieval` selects what the agent searches:

| | `knowledge` (default) | `documents` |
|---|---|---|
| `ingest` | LLM extracts knowledge records (`prompts/extract.md`) | chunks + embeds sources as they are, no LLM |
| Index | `knowledge_items` + `raw_conversations` | `chunks_<label>` (`backend/src/agents/rag`) |
| Agent prompt | `prompts/agent.md` | `prompts/agent_documents.md` |
| Tools | `search_knowledge`, `get_knowledge`, `get_evidence` | `search_documents` (hybrid: vector + BM25, RRF), `get_document` |
| Citations | `[K:<knowledge_id>]` | `[D:<doc_id>]`, doc id = `DatasetSpec.id_column` |
| Evaluation | — | `kb evaluate` on `retrieval_questions` |

Both built-in KBs use `knowledge`. Custom KB definitions can select
`retrieval=Retrieval.DOCUMENTS` and supply `prompts/agent_documents.md` to use
the shared document retrieval implementation. `context_filters` apply to
knowledge retrieval only, since chunks have no extracted context.

For document retrieval, `chunking=ChunkingConfig(max_words=..., overlap_sentences=...)`
sets how sources are split (default: one chunk per source, table `chunks_full`).
Each setup gets its own table, and the stored index config (embedding model/dim,
chunking) refuses to mix setups.
`ingest` skips sources already indexed; `--reindex` rebuilds the table, which is
also how to apply a new embedding model (`reembed` covers knowledge retrieval only).

`evaluate` reports Recall@k, MRR@k, and nDCG@k for semantic, keyword, and hybrid
search on `retrieval_questions` (a CSV with question ids, questions, and gold
source ids separated by `;`), lists the questions whose top result is not gold,
and saves per-question rankings to `data/kb_eval/<kb>/`. `--query-instruction`
compares embedding instructions without re-indexing.

Query embedding instructions belong to each KB's optional `prompts/embedding_query.md`,
loaded as `KnowledgeBaseSpec.embedding_query_instruction`. General supplies a
query instruction for finding conversation knowledge. The shared embedding
client receives the selected KB's instruction through `KnowledgeStore.for_kb`,
for both CLI and API retrieval.
Queries use `Instruct: <instruction>\nQuery: <query>`; document embeddings are
unchanged. For models that do not use this format, leave the file empty/remove
it or pass `embedding_query_instruction=""` in the KB's `SPEC`. An explicit
value in `SPEC` overrides the file.

The old global `EMBEDDING_QUERY_INSTRUCTION` environment variable is no longer
read; move custom instructions into the appropriate KB file. Restart the API
after editing a prompt so its cached agent reloads the configuration. Query-only
instruction changes do not require re-ingestion or re-embedding stored documents.

Records share one structure (subject, claim, context, type, validity); what
differs per KB is which `context` keys matter:

- `context_filters`: each `ContextFilter(key, description)` becomes an optional
  `search_knowledge` argument of the agent, filtering on `context.<key>` (with
  an expression index). Records without the key still match unless
  `include_unscoped=False`. Industrial QA filters on `machine_id`/`machine_model`.
- `embedding_context_keys`: context keys that lead a record's embedded text.
- `DatasetSpec.context_columns`: CSV columns copied into the context of every
  record extracted from the row (e.g. the document's `source_type`).
- `DatasetSpec.id_column`: rows whose id is already processed are skipped by
  `ingest`, so an interrupted run continues where it stopped (`--no-resume`
  to disable).
- `ontology` (optional `ontology.yaml`; Industrial QA uses `catalog/ontology.yaml`): canonical values, aliases and implied
  keys of context keys; extracted context is normalized to it and the changes
  and unknown values are kept in `revision["ontology"]`.
- `record_normalizer`: called with the transcript and its records before the
  ontology, for fixes an exact lookup cannot make. `industrial_qa` uses its
  equipment catalog: curated families, component models and sites in
  `catalog/catalog_types.yaml`, plus the asset IDs, lines and stations of the public
  conversations in the generated `catalog/catalog_assets.yaml` (rebuild with
  `uv run python -m backend.knowledge_bases.industrial_qa.catalog build`). It
  moves asset IDs into `machine_id`, splits "Dong Nai Line 1", fills a missing
  asset from the cited cases or the conversation, and records its changes in
  `revision["catalog"]`. Both files also extend the ontology, so the catalog
  version is part of the extraction provenance (`ontology_sha256`).

Every command takes the KB to act on:
```
uv run python -m backend.scripts.kb list
uv run python -m backend.scripts.kb ingest general --file path/to/conversations.csv --limit 5
uv run python -m backend.scripts.kb ingest general --file path/to/transcript.txt
uv run python -m backend.scripts.kb query general "What did the expert recommend?"
uv run python -m backend.scripts.kb reembed general
uv run python -m backend.scripts.kb reset general --yes
```

For knowledge retrieval, `ingest` processes one transcript at a time by default. With `--batch-size N`
it extracts N transcripts concurrently (at most `--concurrency` LLM calls in
flight, default N) and embeds each batch's knowledge in one call; a failed
transcript is marked `failed` without affecting the rest of its batch. The
summary line reports the elapsed time for comparing settings.

To run extraction on ModelArk's discounted bulk tiers instead of the Coding
Plan endpoint, set the `EXTRACTION_*` variables in `.env` (see `.env.example`):
Online inference (Flex) via `EXTRACTION_SERVICE_TIER=flex`, or the deprecated
Batch Chat API via `EXTRACTION_BASE_URL=.../api/v3/batch` plus a batch endpoint
ID. Both are still one request per transcript, so `--batch-size`/`--concurrency`
work the same; the agent keeps using the `OPENROUTER_*` settings.

For ModelArk/DeepSeek models supporting the `thinking` switch, set
`EXTRACTION_THINKING=disabled` in `.env` to extract directly without spending
the output budget on reasoning. Leave it blank for providers that do not
support this field, or use `enabled` to restore reasoning. See
[BytePlus deep reasoning](https://docs.byteplus.com/en/docs/modelark/deep-thinking).
An error with `finish_reason=length` and `reasoning_tokens=8192` means the
model used the entire CLI output budget before producing JSON. Shortening
the prompt reduces input tokens; disabling reasoning addresses this output
budget problem. Set `EXTRACTION_MAX_TOKENS` to a positive integer to override
the default output budget of 20,000 tokens. Raising the budget permits more
token use rather than saving it.
Batch size controls parallel requests, not tokens per document. Consolidation
has its own switch, `CONSOLIDATION_THINKING=disabled`; when its JSON is cut
off, every lesson of the conversation is added as new and the error is kept in
the record's `revision["consolidation"]`.

## EnterpriseRAG-Bench (`enterprise_rag`)

Internal documents of a fictional company (Redwood Inference) from
[EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench):
Slack, Gmail, Linear, Google Drive, HubSpot, Fireflies, GitHub, Jira, and
Confluence. Only the 722 documents cited by the 500 questions are downloaded
(range requests into the 1.26 GB release zip, ~85 MB transferred), one document
per CSV row. Without the ~500k distractor documents, retrieval scores are
optimistic compared with the official leaderboard.

Extraction version `complete-units-v2` groups knowledge into complete policies,
incidents and tasks: related defaults/conditions/exceptions stay together;
procedures keep ordered commands, prerequisites and validation. A claim may
contain multiple sentences or a short list, and its type describes the unit's
primary purpose. Independent topics and incompatible applicability remain separate.
The prompt requests compact JSON and omits empty context and unknown validity
dates (filled by the application's schema defaults), without imposing an item cap.
Each CLI ingestion saves `metadata.extraction` on the source evidence with the
KB's extraction version, exact prompt SHA-256, model, output token limit and
explicit thinking mode when configured. This also identifies failed attempts.
Check answer quality on a small
sample after changing the prompt or reasoning mode, especially for corrections
and dated decisions. For exceptionally long documents, splitting at sections
or conversation boundaries can reduce each call's budget, but repeated prompts
and overlap can increase total tokens.

`get_evidence` supports character `offset` and `limit` (at most 6000 per call),
returning `total_chars` and `next_offset`. The agent can read subsequent pages
instead of repeatedly seeing only a long document's beginning. The public SSE
trace shows page offsets/counts, not the raw source text.

To rebuild EnterpriseRAG after changing the extraction prompt, stop the API and
any active ingestion first. These commands delete only `kb_enterprise_rag` and
recreate it with fresh extraction and embeddings; the CSV remains on disk:

```bash
uv run python -m backend.scripts.kb reset enterprise_rag --yes
uv run python -m backend.scripts.kb ingest enterprise_rag --limit 20 --batch-size 4 --fail-fast
```

Review that sample before continuing with the rest (processed doc IDs are skipped):

```bash
uv run python -m backend.scripts.kb ingest enterprise_rag --batch-size 4 --fail-fast
```

Restart the API and start a new UI conversation after rebuilding. `reembed` alone
does not regroup existing claims; `--no-resume` adds rows rather than replacing
them. Resetting is unnecessary for later resume runs with the same extraction.

```
uv run python -m backend.knowledge_bases.enterprise_rag.prepare   # -> data/enterprise_rag/
uv run python -m backend.scripts.kb ingest enterprise_rag --limit 20 --batch-size 8
uv run python -m backend.scripts.kb ingest enterprise_rag --batch-size 8   # the rest
uv run python -m backend.knowledge_bases.enterprise_rag.bench retrieval --k 10
uv run python -m backend.knowledge_bases.enterprise_rag.bench answers --limit 20
uv run python -m backend.knowledge_bases.enterprise_rag.bench report
```

`bench answers` writes `data/enterprise_rag/answers.jsonl` in the benchmark's
format (`question_id`, `answer`, `document_ids` of the cited sources) and resumes
where it stopped. Score it for correctness with the benchmark repository's
`python -m src.scripts.answer_evaluation.metrics_based_eval --answers-file ...`
(needs its `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL_NAME`).

The API selects the KB via `/v1/knowledge-agent/<kb>/stream` (or `kb` in the
legacy `/stream` request), lists KBs at `GET /v1/knowledge-agent/knowledge-bases`,
and the Streamlit UI has a KB selector.

## AssetOpsBench (`assetopsbench`)

[IBM AssetOpsBench on Hugging Face](https://huggingface.co/datasets/ibm-research/AssetOpsBench)
contains evaluation scenarios and grading criteria, not an independent document
corpus. This adapter prepares those questions separately and builds a KB from
the public static reference samples in the
[IBM companion repository](https://github.com/IBM/AssetOpsBench/tree/81265cbbd36d7348ec0aeae4b398c86d332f2fc1/src/couchdb/scenarios_data/shared).
No question text, expected answer, hint or failure-event label is ingested.

```bash
uv run python -m backend.knowledge_bases.assetopsbench.prepare
uv run python -m backend.scripts.kb ingest assetopsbench --limit 3 --fail-fast
uv run python -m backend.scripts.kb ingest assetopsbench --batch-size 4 --fail-fast
uv run python -m backend.scripts.kb query assetopsbench "Which sensors are listed for Chiller 6 at MAIN?"
```

Preparation writes `data/assetopsbench/` (gitignored):

- `corpus.csv`: 26 documents from 9 reference files: asset profiles, asset/sensor/
  failure-mode catalogs, failure codes, sample failure modes, TSFM model/feature
  catalogs and work orders. Each source object becomes one document, preserving
  lists, nulls, exact identifiers and sample/non-exhaustive flags. CSV columns
  include `doc_id`, `document`, `source_type`, `title`, `asset_id`, `asset_type`,
  `site_id`, and source path/URL/revision. Stable document IDs support ingestion resume.
- `questions.jsonl`: all 467 scenarios from the six configurations declared in
  the dataset card. Each entry has a namespaced `question_id` (`config:id`),
  `question`, `config`, `split`, source URL/revision and the unchanged original
  `source_record`, including annotations for future evaluation. The upstream
  `hydrolic_pump` spelling is retained. Additional FailureSensorIQ QA variants
  are excluded. Questions have no inferred gold document IDs or coverage labels.
- `manifest.json`: revisions, source/output SHA-256 hashes, counts and scope.
- `raw/`: original files cached by source and immutable revision.

The adapter pins Hugging Face revision `5e25bb7f2cd37fb68b9a9e1f99d170ca5be7ce17`
and GitHub revision `81265cbbd36d7348ec0aeae4b398c86d332f2fc1`. Run preparation
again to rebuild deterministically from cache; `--offline` forbids downloads,
and `--force` downloads the pinned files again. `--output-dir PATH` writes
elsewhere; then pass `--file PATH/corpus.csv` to `kb ingest`. Preparation needs
only the existing dependencies, with no Hugging Face token or LLM calls.

Extraction uses the shared `subject/claim/context/type/validity` schema. Retrieval
uses database `kb_assetopsbench`, English full-text search, its own embedding
instruction and optional `asset_id`, `asset_type`, `site_id`, `source_type`
filters. CSV context overrides model-generated scope. Generic records without
asset/site scope can still match; the agent checks applicability. Source IDs
and capitalization are preserved: `hyd_1` is not silently mapped to scenario
`hp_1`, and `CHILLER6` is not silently mapped to `Chiller 6`.

The registered KB also appears in the API/UI selector. Query through
`POST /v1/knowledge-agent/assetopsbench/stream` with `query` and `thread_id`.
Restart the API after adding the KB. Extraction and ingestion require the
existing LLM, embedding and PostgreSQL settings in `.env`.

**Scope:** this is a static sample KB for the extract → ingest → retrieval flow,
not a reproduction of the complete AssetOpsBench execution environment. Samples
can differ from the snapshots assumed by benchmark questions. Time series,
forecast/RUL execution, anomaly detection and work-order mutations are not
implemented. The agent reports unsupported requests and incomplete inventories.
Full benchmark scoring and verified question-to-evidence mappings are deferred;
do not report scores on these 467 questions as official AssetOpsBench results.
If you change source revisions or extraction behavior after ingestion, rebuild
the KB explicitly; `--force` only refreshes preparation and does not replace
stored records (`--no-resume` would add duplicates).

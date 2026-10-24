# General knowledge base (`general`)

A general-purpose knowledge agent for conversations on any topic. Ingestion
stores each conversation in `raw_conversations`, extracts structured knowledge
into `knowledge_items`, and the ReAct agent answers questions over those records
with `[K:<id>]` citations. Unlike `industrial_qa`, there is no fixed
retrieval/rerank/answer workflow, equipment catalog, ontology, Case Card or
domain-specific scope.

```
general/
  __init__.py          # SPEC: knowledge retrieval, typed output, no filters
  schema.py            # GeneralExtraction: flat list of typed items
  prompts/
    extract.md         # extraction prompt ({RAW_CONVERSATION})
    agent.md           # ReAct agent prompt
    embedding_query.md # query-side embedding instruction
```

## Storage

There are no new tables. The KB uses the shared schema in its own database,
`kb_general` (see `backend/src/storage/knowledge_store.py`):

| Table | Holds |
| --- | --- |
| `raw_conversations` | the transcript as evidence, `stated_by`, `metadata` (dataset row, transcript hash, extraction provenance), `status` pending/processed/failed, `error` |
| `knowledge_items` | one row per extracted item: `subject`, `claim`, `type`, `context` JSONB, validity dates, `evidence_id` → `raw_conversations.id`, `status` (always `draft` on ingest), support/conflict/supersession links, `revision`, `embedding`, `search_text`/`search_vector` |

The KB-specific part is the shape of `knowledge_items.context`:

| Context key | Meaning | Indexed for search |
| --- | --- | --- |
| `certainty` | `stated`, `uncertain` (hypothesis, estimate, disagreement) or `planned` (proposal, next step) | yes |
| `applies_to` | the specific product/system/team/site/version the item is limited to | yes, leads the embedding |
| `condition` | short tag for the situation the item depends on | yes, leads the embedding |
| `speaker` | speaker label as written in the transcript | yes |
| `quotes` | `[{turn_id?, quote}]` verbatim evidence | no (verification and `get_knowledge` only) |
| `steps` | ordered steps of a `procedure`, also rendered into `claim` | no |

Absent keys mean "not limited", so search filters treat them as unscoped.

## Extraction schema

`schema.py` asks the LLM for `{"items": [...]}`. Each item has a `key` (a short
label unique within the response), `type`
(`fact`, `rule`, `procedure`, `heuristic`, `case`), `subject`, `claim`, `steps`,
`certainty`, `applies_to`, `condition`, `speaker`, `quotes` (at least one),
`valid_from`/`valid_to` and `based_on` (keys of the cases a non-case item was
drawn from, stored as `supported_by` positions). Keys replaced list positions in
`general-items-v2`: models often counted positions wrongly, pointing a lesson at
itself or at an unrelated case. Items are written in the speakers' language.
Validation rejects the following: a procedure without at least two steps, steps
on any other type, missing quotes, unknown fields, duplicate keys, and `based_on`
naming an unknown key or a non-case. A failed validation marks the conversation
`failed`; the transcript stays stored. Voice capture asks the LLM once more with
the validation error before failing.

## Agent

The agent is the shared LangGraph ReAct agent (`create_agent`) with
`search_knowledge` (hybrid vector + full-text, at most three searches per question),
`get_knowledge` and `get_evidence`. `prompts/agent.md` makes it keep certainty, scope
and draft status visible. It presents disagreements, abstains without
evidence and cites every important claim. There are no KB-specific
`context_filters`: scope values are free text, so exact-match filters would
miss most records. `subject`, `knowledge_type` and `valid_on` still filter.

## Usage

There is no default dataset. Pass a `.txt`/`.md` transcript or a CSV with a
`raw_conversation` column (optional `stated_by`):

```bash
uv run python -m backend.scripts.kb ingest general --file path/to/conversations.csv --verify quotes
uv run python -m backend.scripts.kb query general "What should we check before rerunning the export job?"
```

Through the API: `POST /v1/knowledge-agent/general/stream`. Transcripts with
`[T01] Speaker: ...` lines get turn-level quote checks; without turn IDs, quotes are
checked against the whole transcript.

**Limitations:** no benchmark or retrieval questions exist for this KB yet, so its
extraction and answer quality are unmeasured. Records are drafts until an expert
reviews them.

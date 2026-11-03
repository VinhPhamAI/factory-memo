# Database schema proposal

**Status: tables created; not used by services.** This document defines the target tables,
relationships, and the work handled by each service. The models in `backend/src/storage/orm/`
and the initial Alembic revisions create these tables, the updated processing tables and the
database rules in a separate database (`APP_DATABASE_URL`); no API, worker or agent
reads or writes them yet, and existing data has not been moved. See
[database migrations](../development/database-migrations.md) for commands and enforced rules.

## Purpose and scope

Support faster machine fault handling and knowledge inheritance through two flows:

- **Creating searchable knowledge:** uploaded files, images, and handwritten notes become
  extracted knowledge that can be reviewed and searched.
- **Fault handling:** an employee discusses a fault with the agent and creates a
  self-contained inquiry. A remote expert can accept, edit, deny, or ask a question.


## What changes from the current system

| Area | Current system | Proposed system |
|---|---|---|
| Backend databases | A shared list of collections and processing jobs, plus one database per knowledge collection | One backend database; collection records use `knowledge_base_id` |
| Original material | `raw_conversations` includes transcripts, pasted text, and converted documents | `knowledge_sources` |
| Search | Embeddings (lists of numbers used to compare meaning) for both extracted `knowledge_items` and original document passages | Search extracted knowledge in `knowledge_chunks` only |
| Files and machines | File information on disk; equipment catalog in configuration files | `files` and `machines`; file bytes stay outside PostgreSQL |
| Users and inquiries | No database tables for accounts or inquiries | Accounts, ownership, inquiries, and response history |
| Standalone agent memory | Messages and saved agent state in `agent_memory` | Keep these; do not add a second backend message table initially |

The old backend chat loses history when restarted. The standalone agent saves
its history in PostgreSQL and keeps it after a restart.

## What each service stores

- One PostgreSQL container hosts the backend database and `agent_memory`.
- The backend owns users, knowledge, files, permissions, and inquiries.
- The agent stores messages and checkpoints. A checkpoint saves the information
  the agent needs to continue answering. LangGraph manages those tables.
- Actual files stay on a Docker volume: storage that remains when a container is
  replaced. Database records store the file location.
- Backend credentials do not grant access to agent memory by default.

## Shared conventions

| Convention | Rule |
|---|---|
| IDs | New application IDs use UUIDv7, a unique identifier containing a creation-time component, stored as `uuid`. Keep existing IDs and IDs managed by LangGraph. |
| Times | `timestamptz` stores a date and time representing a specific moment; display it in the user's time zone. |
| Required fields | All columns require a value unless marked optional or generated. PostgreSQL calculates generated columns automatically. |
| Keys and references | A primary key uniquely identifies a row. A foreign key references an existing row in another table. |
| Structured data | `jsonb` stores named fields and lists in JSON format; the backend checks their required structure and values. |
| Basic types | `text` stores text; `boolean` stores true/false; `bigint` stores whole numbers; `text[]` stores a list of text values. |
| Transactions | A group of database writes that either all succeed or all fail. |
| Editable records | Include the three record-date columns below. |

| Record-date column | Type | Rule |
|---|---|---|
| `created_at` | `timestamptz` | Default `now()`; never changed |
| `updated_at` | `timestamptz` | Default `now()`; maintained on each record update |
| `deleted_at` | `timestamptz`, optional | Default `NULL` (no value); setting a date hides the record without removing it |

UUIDv7 generation belongs in application code on the current PostgreSQL 17 setup.
Its time component does not replace `created_at` or guarantee the order of actions across
services. See the [UUID specification](https://www.rfc-editor.org/rfc/rfc9562.html)
and [PostgreSQL 17 UUID functions](https://www.postgresql.org/docs/17/functions-uuid.html).

## Tables

### 1. `users`

One account per employee, expert, or administrator.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `username` | `text` | Unique; remove surrounding spaces and use a consistent letter case before storage and login |
| `display_name` | `text` | Name shown on screen |
| `password_hash` | `text` | Value derived from the password to check login; never store the original password |
| `role` | `text` | `employee`, `expert`, or `admin` |
| `is_active` | `boolean` | Default `true`; disabled accounts cannot log in |
| Record dates | See conventions | Includes optional `deleted_at` |

Do not reuse deleted accounts' usernames. This design assumes password login.
Login through Google or another provider would need its own account-linking design.

### 2. `machines`

Factory machine identifiers, models, locations, and alternative names.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `code` | `text` | Unique factory identifier; retained after deletion |
| `name` | `text` | Display name |
| `model`, `location` | `text`, optional | Model and line/station |
| `aliases` | `text[]` | Alternative names; default empty array |
| `details` | `jsonb` | Additional specifications; default `{}` |
| Record dates | See conventions | Includes optional `deleted_at` |

Application code uses aliases to recognize alternative names. Relationships between machines and their components,
and links to 3D files, are left for later.

### 3. `files`

Information about uploaded files. Actual file contents are not stored here.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `filename`, `content_type` | `text` | Original name and format, such as `image/png` |
| `size_bytes` | `bigint` | File size in bytes; zero or greater |
| `storage_key` | `text` | Unique storage location; not a public download URL |
| `sha256` | `text` | SHA-256 value calculated from file contents to compare files; duplicate values are allowed |
| `uploaded_by` | `uuid` | References `users.id` |
| Record dates | See conventions | Includes optional `deleted_at` |

File permissions follow the related source or inquiry. A file ID alone does not
grant download access. Processing status belongs to sources and processing jobs.

### 4. `knowledge_bases`

Descriptions of knowledge collections. Their actual content lives in sources and chunks.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `name` | `text` | Unique stable name; retained after deletion |
| `title` | `text` | Display name |
| `description` | `text`, optional | Collection purpose |
| `created_by` | `uuid` | References `users.id` |
| Record dates | See conventions | Includes optional `deleted_at` |

### 5. `knowledge_sources`

One original item submitted to create knowledge: a document, image of handwriting,
or pasted text. Employee-agent chats are not knowledge sources.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `knowledge_base_id` | `uuid` | References `knowledge_bases.id` |
| `file_id` | `uuid`, optional | References `files.id`; required for document/image sources |
| `title` | `text` | Source name |
| `kind` | `text` | `document`, `image`, or `text` |
| `extracted_text` | `text`, optional | Text read from the source; `NULL` before conversion |
| `status` | `text` | `received`, `processing`, `processed`, `failed`; default `received` |
| `metadata` | `jsonb` | Page numbers, text/image positions, and original author information; default `{}` |
| `submitted_by` | `uuid` | References `users.id` |
| Record dates | See conventions | Includes optional `deleted_at` |

Do not store embeddings here. A source can finish processing without producing
chunks if no usable knowledge is found. Keep original files and the evidence used
by existing chunks when processing again. Record attempts and errors separately.

### 6. `knowledge_chunks`

One extracted knowledge record that the system can search and display as a case
card. This table does not store a second set of searchable original document passages.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `knowledge_base_id` | `uuid` | References `knowledge_bases.id` |
| `title`, `content` | `text` | Heading and readable knowledge |
| `details` | `jsonb` | When the knowledge applies, symptoms, ordered steps, cautions, outcomes, and change history; default `{}` |
| `search_text` | `text` | Text assembled from the title, content, and relevant details for searching |
| `embedding` | `vector(D)`, optional | List of numbers used to compare meaning; absent until calculated |
| `search_vector` | `tsvector`, generated | Words prepared by PostgreSQL for keyword searches on `search_text` |
| `status` | `text` | `draft`, `approved`, `deprecated`; default `draft` |
| `reviewed_by` | `uuid`, optional | References `users.id` |
| `reviewed_at` | `timestamptz`, optional | Set together with `reviewed_by` |
| `superseded_by` | `uuid`, optional | References replacement `knowledge_chunks.id`; cannot reference itself |
| Record dates | See conventions | Includes optional `deleted_at` |

- `D` is the number of values required by the selected embedding model. Use one
  model and size initially.
- Dense search compares embeddings. Hybrid search combines that with keywords.
  Both search this table; original source text is retained as evidence.
- After searchable text changes, stop using its old embedding until a new one is
  saved. Changing the model requires recalculating embeddings and rebuilding the
  database structure that speeds up their search.
- Content corrections create a replacement record; preserve the original evidence,
  conditions under which the knowledge is valid, and review history. Changed
  content needs a new approval.
- Unknown symptoms or repair outcomes remain unknown; extraction must not invent them.

### 7. `knowledge_chunk_sources`

Links extracted knowledge to its supporting original material. These references
allow answers to cite the pages, quotations, or images they used.

| Column | Type | Rules and purpose |
|---|---|---|
| `chunk_id` | `uuid` | References `knowledge_chunks.id` |
| `source_id` | `uuid` | References `knowledge_sources.id` |
| `references` | `jsonb` | At least one supporting page number, exact quotation, or marked image area |

The pair `(chunk_id, source_id)` must be unique. One link can list several supporting
passages. Each searchable chunk needs at least one source. Check that both belong
to the same collection when saving the link, as part of the same database transaction.
Links preserve evidence; do not hide or delete them independently.

### 8. `conversations`

Who owns an agent conversation and the information shown in conversation lists.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key; also the agent `conversation_id` / checkpoint `thread_id` |
| `owner_id` | `uuid` | References `users.id` |
| `title` | `text` | Conversation-list title |
| `machine_id` | `uuid`, optional | References `machines.id` |
| Record dates | See conventions | Includes optional `deleted_at` |

Messages stay in `agent_memory`; no backend message table is proposed initially.
`updated_at` records activity known to the backend. It does not prove that the
agent finished an answer.

### 9. `inquiries`

A request that gives a remote expert enough information to understand the fault
without reading the whole conversation. It is composed from the conversation when
the employee triggers Inquiry Creation. The system may suggest that action.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `conversation_id` | `uuid` | References `conversations.id` |
| `requested_by` | `uuid` | References `users.id`; must own the originating conversation |
| `assigned_to` | `uuid` | References an enabled expert/admin in `users.id` |
| `machine_id` | `uuid`, optional | References `machines.id` |
| `title` | `text` | Short issue description |
| `context` | `jsonb` | Submitted situation, proposed fix, and evidence; fields below |
| `status` | `text` | `open`, `in_review`, `needs_info`, `answered`, `closed`; default `open` |
| Record dates | See conventions | Includes optional `deleted_at` |

The backend checks these fields in `context`:

| Field | Content |
|---|---|
| `situation` | Problem summary and machine information at submission |
| `observations` | Reported symptoms and uncertainty |
| `attempted_actions` | Actions actually performed and reported results |
| `proposed_fix` | Suggested steps awaiting confirmation; may be absent if unknown |
| `evidence` | Saved knowledge IDs, quoted content, review status, and source/file references |

Preserve the submitted title, context, machine, and requester. Later corrections
are events. The expert must understand the situation without reading the whole
conversation or depending on knowledge records that may change later. Do not include
the model's private internal reasoning.

### 10. `inquiry_events`

Permanent expert decisions, questions, revised solutions, and employee replies.

| Column | Type | Rules and purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `inquiry_id` | `uuid` | References `inquiries.id` |
| `actor_id` | `uuid` | References `users.id` |
| `kind` | `text` | `accepted`, `edited`, `denied`, `question`, `reply`, `closed` |
| `content` | `text`, optional | Revised solution, question, reply, or decision explanation |
| `created_at` | `timestamptz` | Default `now()` |

Require content for edits, questions, replies, and denial reasons. Keep earlier
events unchanged; corrections create another event. No `updated_at` or ordinary deletion.
Save the event and new inquiry status in one database transaction: both succeed
or neither does. Prevent two responses from changing the inquiry at the same time
by locking its row while checking and saving a response.

### 11. `inquiry_files`

Files supplied with the original inquiry or a later response.

| Column | Type | Rules and purpose |
|---|---|---|
| `inquiry_id` | `uuid` | References `inquiries.id` |
| `file_id` | `uuid` | References `files.id` |
| `event_id` | `uuid`, optional | References `inquiry_events.id`; absent for original submission |

The pair `(inquiry_id, file_id)` must be unique. The event must belong to the same inquiry.
Each file is attached once per inquiry; later responses can refer to it.
Original attachments remain unchanged after submission.

## Relationships

- A user can own many conversations, upload many files, and submit many sources.
- A user can create many inquiries; experts can create their own inquiries too.
- An expert can receive many inquiries. Each inquiry has one assigned expert.
- A knowledge base can contain many sources and many chunks.
- A source can support many chunks. A chunk can use many sources through
  `knowledge_chunk_sources`.
- A source has zero or one original file. A file can be referenced by several records.
- A machine can be associated with many conversations and inquiries.
- A conversation belongs to one user and can produce many inquiries. Its agent
  saved conversation uses the same ID. PostgreSQL does not enforce a reference
  between these separate databases.
- An inquiry can have many events and files through `inquiry_files`.

## Permissions and inquiry progress

| Action | Employee | Expert | Admin |
|---|---|---|---|
| Ask questions and submit own inquiries | Yes | Yes | Yes |
| Read active knowledge collections | Yes | Yes | Yes |
| Create/edit collections and review knowledge | No | Yes | Yes |
| Respond as assigned reviewer | No | Yes | Yes |
| Manage users and roles | No | No | Yes |

- Users see inquiries they created. Experts also see those assigned to them;
  admins can administer all inquiries.
- Receiving an inquiry does not grant access to the full conversation. Normal
  conversation access remains limited to its owner for every role.
- Restricting individual collections to selected users and automatically choosing
  an expert are left for later.

| Action | Inquiry status |
|---|---|
| Employee submits | `open` |
| Assigned reviewer starts review | `in_review` |
| Reviewer asks a question | `needs_info` |
| Requester replies | `in_review` |
| Reviewer accepts, edits, or denies | `answered` |
| Requester closes after a response | `closed` |

A closing event can record the employee's reported repair outcome. Expert
confirmation alone is not proof that a repair was performed. Reassignment and
reopening need further rules and are left for later.

## How the backend gets conversation history

1. The backend creates the conversation and checks ownership on each request.
2. It calls the agent using the conversation ID and service token: the secret used
   to authenticate backend-to-agent requests.
3. The agent saves its messages/checkpoints and streams the answer.
4. To load past messages, the backend would call a new agent history API protected
   by the service token. This API still needs to be built.

- The backend does not read LangGraph's internal tables directly.
- Loading history initially reads the full saved conversation. It does not fetch
  only a page of messages, such as the latest 20. Measure time and memory use
  before adding another message store.
- History depends on agent availability. Submitted inquiries remain independently
  readable through their saved context.
- `complete.answer` confirms successful generation and checkpoint saving. A lost
  connection can hide that event even if saving succeeded. Do not treat partial
  text as completion or retry blindly.
- The current API cannot prevent duplicate submissions or retrieve a particular
  request's result after its response is lost.
- Future attachments need file references in saved message state, not file bytes.
  The current agent does not yet implement attachment handling.

## Search, hiding records, and lookup speed

- Search only `knowledge_chunks`; check the collection and whether each result
  is allowed to appear.
- Drafts remain searchable with their status shown. Exclude retired (`deprecated`) or deleted
  chunks, deleted collections, and chunks with deleted supporting sources/files.
- Apply the same inclusion rules to keyword and embedding results. Review
  affected knowledge before restoring it after evidence changes.
- Check `deleted_at` when reading or searching records, not just when displaying
  them. Hiding a conversation does not erase checkpoints or delete submitted inquiries.
- Reject file deletion while preserved knowledge/inquiries require it. Permanent
  file removal and checkpoint erasure need separate rules for how long data is kept.
- Deleting one record must not automatically erase linked user, knowledge, or inquiry history.

An index accelerates database lookups. Initial indexes should cover:

| Lookup | Index |
|---|---|
| Collection content | `knowledge_base_id` on sources/chunks |
| Keyword and dense search | GIN (PostgreSQL's keyword-search index) on `search_vector`; an embedding-search index compatible with the number of embedding values |
| Active conversations | `(owner_id, updated_at, id)` |
| Requested/assigned inquiries | Separate `(requested_by, updated_at, id)` and `(assigned_to, updated_at, id)` |
| Inquiry history | `(inquiry_id, created_at, id)` |
| Find chunks/attachments from a source/file | `knowledge_chunk_sources.source_id`, `inquiry_files.file_id` |

Also add indexes for frequently followed table references. Use database rules to
reject unknown roles, statuses, and kinds. The backend checks who may perform each action.

## Existing processing tables

Keep these existing support tables and their behavior initially; update their references.
They do not introduce another collection of searchable content.

| Table | Purpose | Required update |
|---|---|---|
| `ingestion_jobs` | Pending processing work, retries, assigned worker, and recovery after interruption | Use shared collection/source IDs; keep one active job per source and save a completed stage together with its next job |
| `extraction_runs` | Processing attempt settings/results and checks for identical content | Reference `knowledge_sources`; keep the values used to identify matching content/settings and the attempt history |
| `content_signatures` | Near-duplicate warnings | Reference `knowledge_sources`; keep the confirmation step for similar content |
| `knowledge_conflicts` | Suspected conflicts and human review | Reference `knowledge_chunks`; preserve decisions and replacement links |

Implemented models of these tables:

- Replace the text collection name with `knowledge_base_id`, and evidence IDs with
  `source_id` or chunk IDs. Composite foreign keys on `(id, knowledge_base_id)` keep
  each record in the same collection as its source or chunks.
- Drop the job `source_kind`; the source row has its kind.
- Add `knowledge_base_id` to unique keys that held within one `kb_<name>` database,
  such as the active-content key of `extraction_runs`.
- Store `job_id` as a reference to `ingestion_jobs`.

The old original-document chunk tables, keyword-scoring tables, and `rag_indexes`
are not part of the proposed search storage. Stop using them only after data has
been moved and search checks pass.
Keep original passages needed to resolve historical citations.

## Moving existing data and checking the change

Move the data in stages. Renaming tables alone is not sufficient.

| Existing data/code | Required change |
|---|---|
| Registry and `kb_<name>` databases | Create `knowledge_bases` records; move data into shared tables with collection IDs; check for repeated IDs before combining databases |
| `raw_conversations` | Map to `knowledge_sources`; preserve text, original authors, metadata, and IDs, including legacy audio/transcript evidence |
| `knowledge_items` | Map to `knowledge_chunks`; keep types, when knowledge applies, dates of validity, change history, and support/conflict/replacement links in documented `details` fields |
| Existing `evidence_id` | Create source links with original references; keep a mapping so old requests and citations still find their evidence |
| Upload metadata and equipment catalog | Populate `files` and `machines`; keep policy and prompt definitions in code |
| Missing historical user accounts | Use a documented account for imported records where ownership is unknown; do not invent authors or approval |
| Search, database exports, and evaluation runs | Use shared collections and the single search model; keep old evidence references working and test data separate from normal data |
| Standalone agent | Add history access and checks for saved evidence and attachment references |

Before considering implementation complete:

1. Use numbered database-change files for the backend, and keep automated
   LangGraph database setup separate.
2. Test on database copies; compare record counts and links; keep a way to restore
   the previous setup. Do not delete old databases during the initial import.
3. Test that searches stay within the selected collection, plus dense/hybrid
   search, evidence, duplicate handling,
   review history, and replacements.
4. Test permissions, question/reply/decision flows, simultaneous responses, file access,
   and deletion filters.
5. Test unavailable agent history and interrupted streaming without claiming that
   failed or unknown requests completed successfully.

## Related guides

- [Knowledge processing and review](../features/knowledge-management.md)
- [Document uploads](../features/document-uploads.md)
- [Current conflict detection](../features/conflict-detection.md)
- [Agent API](../../agent/docs/api.md)
- [Agent setup and development](../../agent/docs/development.md)

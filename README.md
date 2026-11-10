# FACTORY MEMO

**Faster machine fault handling and knowledge inheritance for factory employees.**

Start with the [product requirements](docs/features/project-overview.md),
[engineering standards](docs/development/code-standards.md), and
[documentation index](docs/README.md). New sessions should read [AGENTS.md](AGENTS.md).

The current code includes signed-in agent chat, a personal file library,
application-backed knowledge ingestion/search/review, expert inquiries, voice
capture and a React UI. Knowledge-base chat tools are optional. Follow the
[local chat setup](docs/features/conversations.md#run-locally) to enable them.
The independent agent has its own
[setup, API and development guide](agent/README.md).

## 1. Environment setup

Docker backend requirements: **Docker with Compose v2.24+**, **Bash**,
**Python 3.12+** and **uv**. The migration script runs Alembic through `uv` on
the host. **make** is optional. Run all commands from the repository root.

### First-time Docker backend setup

On first use, copy `.env.example` to `.env` and configure the chat and embedding
settings described below. Keep an existing `.env` when rerunning setup.

```bash
cp .env.example .env  # first use only, then edit provider/embedding settings
bash deployments/migrate.sh
make setup
# Equivalent without make: bash deployments/setup.sh
```

`migrate.sh` starts Compose PostgreSQL, creates the application database when
needed, applies every Alembic revision, runs `alembic check` and prints the
current revision. It requires `APP_DATABASE_URL` (or
`MIGRATION_DATABASE_URL`) and `EMBEDDING_DIM` in `.env`. It never uses
`DATABASE_URL` as its migration target.

Setup runs sequentially:

1. Check Docker/Compose and configuration; build the shared backend image.
2. Stop existing API/worker containers so they cannot write during restore.
3. Start PostgreSQL and wait for its healthcheck.
4. Initialize the legacy `knowledge_base_registry` and `ingestion_jobs` tables
   in `DATABASE_URL`. Application tables are created only by `migrate.sh`.
5. Run the legacy `backend.knowledge_bases.industrial_qa.snapshot pull` in a
   temporary container, using the API image, `.env`, internal database URL and
   data volumes. Only `benchmark/public/` is additionally mounted, read-only.
   No host `uv sync` is required. This rebuilds embeddings and can incur
   embedding-provider costs.
6. Start API and wait for `/healthz`, then start worker and show service status.

**Seed failure is non-fatal:** its output is saved to `data/setup/seed.*`, a
warning is repeated at the end, and API/worker still start. Setup exits 0 if
the remaining steps succeed; build, database or service-start failures exit
nonzero. Restore can leave partial data after a failure; setup does not roll it
back or delete unrelated records. A running worker container is reported as
started; setup does not submit a real LLM extraction job to validate it.

The default seed uses `HF_DATASET_REPO_ID` from `.env` (the example points to
`duonganh2973/industrial_qa`), run ID
`industrial-qa-test-full-20261010T164959Z` and output directory
`data/industrial_qa_runs/restored-test`. Configure private-repo access with
`HF_TOKEN`. Override snapshot options or skip seed on subsequent starts:

```bash
bash deployments/setup.sh -- --repo-id your-org/industrial_qa --revision COMMIT_OR_TAG
make setup SETUP_ARGS='--skip-seed'
bash deployments/setup.sh --no-build --skip-seed  # reuse the existing image
```

Rerunning retains PostgreSQL/upload volumes and host `data/`; it does not reset
either database. A restore into unrelated legacy knowledge records is refused
and reported as a seed warning. If `.env` is missing, setup creates it from
`.env.example`; provider settings still need to be filled in for seed/QA to work.
The API defaults to **http://localhost:8005** (`/healthz`, `/docs`).
Speech and frontend are optional separate steps below.

### Update the backend during development

After pulling backend code, changing dependencies or adding migrations, update
the application schema and recreate both services from the shared image:

```bash
bash deployments/migrate.sh

docker compose --env-file .env \
  -f deployments/docker-compose.yml \
  up -d --build --force-recreate api worker
```

The migration runs before the new containers start. Existing API and worker
containers may continue running during it, so stop them first when a migration
is not backward-compatible with the currently running code. Building an image
alone does not replace a running container.

### Host Python environment

```bash
uv venv --python 3.12
uv sync
# If .env has not already been created: cp .env.example .env
```

`uv run` automatically uses `.venv`. To activate it manually on macOS/Linux:
`source .venv/bin/activate`.

Edit `.env` with your provider's settings. Add the shared `OPENROUTER_*` values
below; they are supported by the code but absent from `.env.example`.
Replace every placeholder.

```dotenv
# Legacy CLI, benchmark, snapshot registry and per-KB databases
DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent

# Serving API and worker application schema
APP_DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent_app

# Shared OpenAI-compatible chat endpoint
OPENROUTER_API_KEY=<your-api-key>
OPENROUTER_MODEL=<your-chat-model>
OPENROUTER_BASE_URL=<your-chat-api-base-url>

# OpenAI-compatible embedding endpoint
EMBEDDING_API_KEY=<your-api-key>
EMBEDDING_MODEL=<your-embedding-model>
EMBEDDING_BASE_URL=<your-embedding-api-base-url>
EMBEDDING_DIM=<model-output-size>
```

`EMBEDDING_DIM` must match the model's vector size. A local embedding server can
omit its API key when `EMBEDDING_BASE_URL` is set. Keep populated `.env` files out
of Git.

| Optional settings | Purpose |
| --- | --- |
| `EXTRACTION_*`, `VERIFY_*`, `CONSOLIDATION_*`, `ANSWER_*` | Override shared chat settings for each step. |
| `MODELARK_BASEURL`, `MODELARK_APIKEY`, `MODELARK_MODELNAME` | Benchmark generation and LLM judging. Runners fall back to extraction/shared chat settings for judging. |
| `HF_TOKEN` | Access private snapshots; public pulls need no token. |

## 2. Database initialization

The repository uses separate application and legacy storage paths:

| Setting | Current use |
| --- | --- |
| `APP_DATABASE_URL` | Serving API and worker: users, sessions, conversations, files, inquiries, knowledge bases, sources, chunks, application ingestion jobs and conflict review. |
| `DATABASE_URL` | Legacy CLI, benchmark and snapshot registry/job queue; legacy knowledge remains in per-KB databases such as `kb_industrial_qa`. |
| `MIGRATION_DATABASE_URL` | Optional Alembic-only setup-owner connection; it takes precedence over `APP_DATABASE_URL` and is never used at runtime. |

Create or upgrade the application database from the repository root:

```bash
bash deployments/migrate.sh
```

PostgreSQL is exposed on **localhost:5434** by default. The serving API does not
create or alter application tables on startup. Alembic owns that schema. The
application catalog and all serving knowledge records live in the application
database; UI-created knowledge bases copy the General policy. See
[database migrations](docs/development/database-migrations.md) and the
[KB guide](backend/knowledge_bases/README.md#knowledge-bases-created-in-the-ui).

For legacy Industrial QA benchmarking, restore the published knowledge snapshot:

```bash
uv run python -m backend.knowledge_bases.industrial_qa.snapshot pull \
  --repo-id duonganh2973/industrial_qa \
  --run-dir data/industrial_qa_runs/restored-test \
  --run-id industrial-qa-test-full-20261010T164959Z
```

This populates the legacy `kb_industrial_qa` database, rebuilds
knowledge/manual embeddings and restores selected benchmark artifacts when
bundled. It does not populate the application knowledge tables used by the
signed-in React UI. It requires a compatible snapshot and matching local
`benchmark/public/` files. Use a fresh database or one containing the same
snapshot; unrelated records cause the import to stop.

To build knowledge from scratch instead, use the fresh benchmark workflow below.
See the [snapshot guide](backend/knowledge_bases/industrial_qa/SNAPSHOT.md) for
local imports and other restore options.

### Optional: AI services (speech, OCR, embeddings and chat)

All self-hosted AI services are reached through one port: `ai-services-api`
(`127.0.0.1:8080`), a FastAPI app that forwards each request to the worker
owning its path. The workers run in the AI overlay `deployments/ai.compose.yml`
and need an NVIDIA GPU and the NVIDIA Container Toolkit:

| Service | Paths | Worker | GPU memory |
| --- | --- | --- | --- |
| Speech: VieGem-ASR Vietnamese speech recognition, no speaker diarization | `/v1/audio/transcriptions` (HTTP and realtime WebSocket) | `ai-speech` | about 2.4 GB |
| OCR: PaddleOCR-VL-1.6 for scanned PDF, PNG and JPEG ingestion with located regions and Markdown | `/v1/ocr` | `ai-ocr`, `ai-ocr-vlm` | about 7 GB |
| Embeddings: microsoft/harrier-oss-v1-0.6b on TEI, 1024 dimensions (also on port 8001) | `/v1/embeddings` (OpenAI-compatible), `/v1/embeddings/tokenize`, `/v1/embeddings/info` | `ai-embedding` | about 1.5 GB |
| Chat: Qwen/Qwen3.5-4B on vLLM, text only with tool calling (also on port 8043) | `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/v1/messages` | `ai-llm` | controlled by `LLM_GPU_MEM_UTIL` |

```bash
bash deployments/speech/setup.sh                 # speech weights, once
bash deployments/ai-services-api/up.sh speech ocr embedding llm  # or only those you need
bash deployments/speech/smoke_test.sh [speech.wav ["reference transcript"]]
bash deployments/ocr/smoke_test.sh [document.pdf]
bash deployments/embedding/smoke_test.sh
```

See the [AI services API guide](deployments/ai-services-api/README.md), the
[speech](deployments/speech/README.md), [OCR](deployments/ocr/README.md),
[embedding](deployments/embedding/README.md) and
[LLM](deployments/llm/README.md) service guides. To use the local
embeddings in the backend, set `EMBEDDING_BASE_URL=http://127.0.0.1:8080/v1`
and the values in the embedding guide. Speech transcribes audio into
transcript turns for the UI's voice capture, which saves the original audio to
`data/audio/` and ingests the transcript into the selected knowledge base; see
[voice capture](docs/features/voice-capture.md) for storage and schema.

## 3. API and QA UI

**API in Docker Compose** (loads provider settings from the root `.env`):

```bash
docker compose --env-file .env -f deployments/docker-compose.yml up -d --build api
docker compose -f deployments/docker-compose.yml logs -f api
```

This also starts PostgreSQL and waits for it to be healthy. The API is available
at **http://localhost:8005** (`/healthz` for health, `/docs` for API documentation).
Set `API_PORT` in `.env` to change the host port. Recordings and other runtime
data under `/app/data` persist through the host `data/` bind mount. Document
uploads and personal library files use the `document_uploads` and `file_library`
named volumes, so container and host-run services do not share those two stores
by default. Use the development update flow above after changing backend code,
dependencies or migrations.

**Job workers** run Convert, Extract and conflict-check jobs for uploaded files,
recordings and explicit processing actions. Without a worker, jobs stay queued:

```bash
docker compose --env-file .env -f deployments/docker-compose.yml up -d --build worker
docker compose -f deployments/docker-compose.yml logs -f worker
```

The `worker` service uses the API image and volumes and runs one process per
stage slot by default. Configure the counts with `WORKER_CONVERT_PROCESSES`,
`WORKER_EXTRACT_PROCESSES` and `WORKER_CONFLICT_PROCESSES`. See
[job queue and workers](docs/features/knowledge-management.md#job-queue-and-workers).

Inside Compose, the API connects to `postgres:5432` and `ai-services-api:8080`;
`API_DATABASE_URL` and `API_SPEECH_API_URL` override these defaults. Sign-in
and the served knowledge source/job pipeline use the migrated application
database (`APP_DATABASE_URL`; in Compose, `API_APP_DATABASE_URL`). The worker
requires the same URL. `AUTH_ALLOWED_ORIGINS` configures browser access; agent
chat needs `AGENT_API_TOKEN` and reaches the agent at `host.docker.internal:8010`
(`API_AGENT_BASE_URL` overrides; see [agent conversations](docs/features/conversations.md)). See
[authentication](docs/features/authentication.md). For chat or embedding
endpoints running on the Docker host, replace `localhost` in the corresponding
`*_BASE_URL` with `host.docker.internal`. `VOICE_AUDIO_DIR` inside the API
container is fixed to `/app/data/audio`, backed by the host's `data/audio/`.

AI services are optional: prepare speech with `deployments/speech/setup.sh`, then
start them with `deployments/ai-services-api/up.sh` as above.
`deployments/docker-compose.yml` alone starts the API, the worker and
PostgreSQL. After setup, add the AI overlay to start all configured AI services
in the same Compose project and network. GPU use depends on the enabled models
and their memory settings:

```bash
docker compose --env-file .env -f deployments/docker-compose.yml \
  -f deployments/ai.compose.yml up -d --build
```

Commands that use only the base file report the AI containers (`ai-*`) as
orphans; ignore the warning, and do not pass `--remove-orphans` unless they
should stop. Text-layer document conversion can run without OCR; scanned PDFs
and images require `OCR_API_URL`. QA still requires configured chat and
embedding providers, which may be remote instead of this local AI stack.

**Alternative — API on the host:**

```bash
uv run uvicorn backend.app:app --host 0.0.0.0 --port 8005
uv run python -m backend.worker   # separate terminal; --convert N --extract M
```

**Terminal 2 — React QA UI** (Node.js 22.12+, run from `frontend/`):

```bash
npm ci
cp .env.example .env.local
npx vite --host 127.0.0.1 --port 5180 --strictPort
```

Open **http://127.0.0.1:5180** and sign in. Follow the
[chat setup](docs/features/conversations.md#run-locally) to enable KB tools.
The default `VITE_DATA_MODE=api` connects to FastAPI through the Vite proxy.
If you already have `.env.local`, set its data mode to `api` and restart Vite.
**Thư viện tài liệu** (`/files`) keeps each signed-in user's own PDF, XLSX, DOCX,
JPEG and PNG files, with bounded previews or excerpts. **Tải tài liệu lên** adds
new files to the default KB chosen by an administrator in **Kho kiến thức**; see
[file library](docs/features/file-library.md). **Yêu cầu chuyên gia**
(`/inquiries`) preserves inquiry drafts, attached evidence, submission and expert
review separately from reusable knowledge; see
[expert inquiries](docs/features/inquiries.md).
The legacy `/capture` page is not in the navigation. Its **Ghi âm** tab records a
conversation (needs the speech service and HTTPS or localhost for the microphone), and
its **Tải lên tài liệu** tab attaches files to a selected KB with optional Auto
conversion/extraction; see [document uploads](docs/features/document-uploads.md). See the
[frontend guide](frontend/README.md) for configuration, tests and optional mock demo.

## 4. Factory benchmark

The [dataset manifest](benchmark/benchmark/manifest.json) records **3 manuals,
1,067 chunks, 195 synthetic conversations and 500 questions**: **100 train /
400 test**. All benchmark content is in English.

### Score restored predictions

After restoring the snapshot, recompute retrieval and multiple-choice metrics
without LLM calls:

```bash
uv run python benchmark/evaluate.py \
  --predictions data/industrial_qa_runs/restored-test/predictions.jsonl \
  --split test \
  --question-ids data/industrial_qa_runs/restored-test/question_ids.txt \
  --skip-llm \
  --out data/industrial_qa_runs/restored-test/results_recomputed.json
```

This scores saved predictions; it does not generate new answers. Remove
`--skip-llm` to judge answers with the configured `MODELARK_*` endpoint.

### Run fresh extraction, answers and evaluation

These runs call LLM/embedding endpoints and incur usage costs. **Ingestion
replaces unrelated records in `kb_industrial_qa`**, retaining the current and
explicitly reused runs. Stop API/UI use of this KB while rebuilding it.

Run a **10-question train pilot**, then expand to **all 100 train questions**:

```bash
uv run python -m backend.knowledge_bases.industrial_qa.benchmark.pilot all \
  --run-dir data/industrial_qa_runs/pilot-local \
  --split train --sample 10

uv run python -m backend.knowledge_bases.industrial_qa.benchmark.train all \
  --run-dir data/industrial_qa_runs/train-local \
  --from-run-dir data/industrial_qa_runs/pilot-local
```

Freeze model, prompt and retrieval settings before running **all 400 test
questions**:

```bash
uv run python -m backend.knowledge_bases.industrial_qa.benchmark.holdout all \
  --run-dir data/industrial_qa_runs/test-local \
  --reuse-run-dir data/industrial_qa_runs/train-local
```

Each run saves checkpoints/configuration, `answers.json`, `predictions.jsonl`
and `results.json`. Repeat the same command to resume; use new run directories
for new experiments. Adding `--skip-llm` skips judges only: extraction and answer
generation still call models.

Use only `benchmark/public/` as inference data. Hidden labels in
`benchmark/generation/` and `benchmark/benchmark/` are for construction and
evaluation. Tune on train and report pilot coverage separately from full scores.

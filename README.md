# FACTORY MEMO

**A solution for capturing expert manufacturing know-how and turning it into traceable, reusable knowledge for engineers and AI.**

This repository implements text-based knowledge extraction, storage, retrieval
and evidence-grounded answers, with a synthetic benchmark, a React QA UI and a
Streamlit trace UI.
Live voice capture (React UI tab **Ghi âm**) records the microphone, transcribes
it with the optional ASR + diarization service, stores the transcript as evidence
and extracts draft knowledge for side-by-side review (see
[voice capture](docs/features/voice-capture.md)). The **Tải lên tài liệu** tab stores original
documents (see [document uploads](docs/features/document-uploads.md)); its **Auto** option
queues conversion and extraction right after saving.
**Quản lý tri thức** tracks raw, converted and extracted sources, with per-source
mode selection and shared review for transcripts, knowledge items and document
chunks. Convert and Extract are queued jobs run by background workers
(`uv run python -m backend.worker`, Compose service `worker`); Extract on a raw
TXT converts it first. OCR is not yet
implemented; see [knowledge management](docs/features/knowledge-management.md).
Multimodal extraction, location awareness and
QR identification are planned capabilities.

The independent agent service has its own [setup, API, and development guide](agent/README.md).

See the [documentation index](docs/README.md) for architecture, features, and development guides.

## 1. Environment setup

Docker backend requirements: **Docker with Compose v2.24+** and **Bash**;
**make** is optional. **Python 3.12+** and **uv** are needed only for commands
run on the host (benchmarking, manual snapshot commands, or a host-run API).
Run all commands from the repository root.

### Docker backend with one command

On first use, copy `.env.example` to `.env` and configure the chat and embedding
settings described below. Keep an existing `.env` when rerunning setup.

```bash
cp .env.example .env  # first use only, then edit provider/embedding settings
make setup
# Equivalent without make: bash deployments/setup.sh
```

Setup runs sequentially:

1. Check Docker/Compose and configuration; build the shared backend image.
2. Stop existing API/worker containers so they cannot write during restore.
3. Start PostgreSQL and wait for its healthcheck.
4. Initialize `knowledge_base_registry` and `ingestion_jobs` using the container's
   database connection. Database/authentication/DDL failures stop setup.
5. Run `backend.knowledge_bases.industrial_qa.snapshot pull` in a temporary
   container, using the API image, `.env`, internal database URL and data volumes.
   Only `benchmark/public/` is additionally mounted, read-only. No host `uv sync`
   is required. This rebuilds embeddings and can incur embedding-provider costs.
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
the database. A restore into unrelated knowledge records is refused and reported
as a seed warning. If `.env` is missing, setup creates it from `.env.example`;
provider settings still need to be filled in for seed/QA to work.
The API defaults to **http://localhost:8005** (`/healthz`, `/docs`).
Speech and frontend are optional separate steps below.

### Host Python environment (optional)

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
DATABASE_URL=postgresql+psycopg://voice_agent:voice_agent@localhost:5434/voice_agent

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

The following commands are the manual alternative to `make setup`.

Start PostgreSQL + pgvector and check readiness:

```bash
docker compose -f deployments/docker-compose.yml up -d postgres
docker compose -f deployments/docker-compose.yml exec postgres pg_isready -U voice_agent -d voice_agent # test db
```

PostgreSQL is exposed on **localhost:5434**. The application creates each
`kb_<name>` database, tables and vector extension on first use. The built-in
registry contains `industrial_qa` and `general`; further knowledge
bases can be created in the UI and are recorded in the `knowledge_base_registry`
table (see the [KB guide](backend/knowledge_bases/README.md#knowledge-bases-created-in-the-ui)).

For the Industrial QA demo, restore the published knowledge snapshot:

```bash
uv run python -m backend.knowledge_bases.industrial_qa.snapshot pull \
  --repo-id duonganh2973/industrial_qa \
  --run-dir data/industrial_qa_runs/restored-test \
  --run-id industrial-qa-test-full-20261010T164959Z
```

This populates `kb_industrial_qa`, rebuilds knowledge/manual embeddings and
restores the selected benchmark artifacts when bundled. It requires a compatible
snapshot and matching local `benchmark/public/` files. Use a fresh database or
one containing the same snapshot; unrelated records cause the import to stop.

To build knowledge from scratch instead, use the fresh benchmark workflow below.
See the [snapshot guide](backend/knowledge_bases/industrial_qa/SNAPSHOT.md) for
local imports and other restore options.

### Optional: speech service

`speech-api` (NeMo-Speech.cpp with Nemotron 3.5 streaming ASR and Nemotron 3
diarization) needs an NVIDIA GPU and the NVIDIA Container Toolkit:

```bash
bash deployments/speech/setup.sh && bash deployments/speech/up.sh
bash deployments/speech/smoke_test.sh
```

See the [speech service guide](deployments/speech/README.md). It transcribes
audio into speaker turns for the UI's voice capture, which saves the original
audio to `data/audio/` and ingests the transcript into the selected knowledge
base; see [voice capture](docs/features/voice-capture.md) for storage and schema.

## 3. API and QA UI

**API in Docker Compose** (loads provider settings from the root `.env`):

```bash
docker compose --env-file .env -f deployments/docker-compose.yml up -d --build api
docker compose -f deployments/docker-compose.yml logs -f api
```

This also starts PostgreSQL and waits for it to be healthy. The API is available
at **http://localhost:8005** (`/healthz` for health, `/docs` for API documentation).
Set `API_PORT` in `.env` to change the host port. Recordings and other runtime
data persist in the host's `data/` directory across container recreation.
Document uploads use the separate Docker-managed `document_uploads` volume,
mounted at `/app/data/uploads/`; host-run APIs default to `data/uploads/`.
Rebuild with the same command after changing backend code or dependencies.

**Job workers** run Convert/Extract jobs (Auto uploads and recordings, and the
Convert/Extract buttons in **Quản lý tri thức**). Without a worker, jobs stay queued:

```bash
docker compose --env-file .env -f deployments/docker-compose.yml up -d --build worker
docker compose -f deployments/docker-compose.yml logs -f worker
```

The `worker` service uses the API image and volumes and runs one process per
stage slot (`WORKER_CONVERT_PROCESSES`, `WORKER_EXTRACT_PROCESSES`, default 1
each). See [job queue and workers](docs/features/knowledge-management.md#job-queue-and-workers).

Inside Compose, the API connects to `postgres:5432` and `speech-api:8080`;
`API_DATABASE_URL` and `API_SPEECH_API_URL` override these defaults. For chat or
embedding endpoints running on the Docker host, replace `localhost` in the
corresponding `*_BASE_URL` with `host.docker.internal`. `VOICE_AUDIO_DIR` inside
the API container is fixed to `/app/data/audio`, backed by the host's `data/audio/`.

Speech is optional: prepare it with `deployments/speech/setup.sh`, then start it
with `deployments/speech/up.sh` as above. After setup, the full stack can also be
started with `docker compose --env-file .env -f deployments/docker-compose.yml
--profile speech up -d --build`. Without that profile, `up -d --build` starts
the API, the worker and PostgreSQL. Document uploads and QA do not require speech.

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

Open **http://127.0.0.1:5180**, select a knowledge base, and ask a question. The
default `VITE_DATA_MODE=api` connects QA to FastAPI through the Vite `/v1` proxy.
If you already have `.env.local`, set its data mode to `api` and restart Vite.
The **Ghi âm** tab records a conversation (needs the speech service and HTTPS or
localhost for the microphone); after Stop it shows the transcript next to the
extracted knowledge for approval. **Tải lên tài liệu** stores original documents
without creating a conversation or extracting content; see [document uploads](docs/features/document-uploads.md). See the
[frontend guide](frontend/README.md) for configuration, tests and optional mock demo.

**Alternative — Streamlit trace UI:**

```bash
uv run streamlit run streamlit_app.py
```

Open **http://localhost:8501**, select **industrial_qa**, and ask a question.
The UI connects to the API on port **8005** and displays answers, citations and
tool traces. Check API health at **http://localhost:8005/healthz**.
Conversation history is cleared when the API restarts.

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

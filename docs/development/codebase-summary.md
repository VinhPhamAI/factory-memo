# Codebase map

**Purpose:** locate current code and instructions. This is not a recommendation to
copy every existing pattern. Use [engineering standards](code-standards.md) for new work.

## Find the implementation

| Location | What to find |
|---|---|
| `agent/` | Independent Python agent package, dependency lock, CLI, tests, and Dockerfile |
| `agent/src/denso_agent/` | Agent API, graph/runtime, provider client, checkpoint access, setup commands |
| `backend/app.py`, `backend/api/` | Existing backend HTTP APIs |
| `backend/src/services/` | Knowledge processing, extraction, and source handling |
| `backend/src/storage/` | Current database access and knowledge/search records |
| `backend/src/agents/rag/` | Existing original-document indexing and search |
| `backend/src/agents/agent/` | Older backend agent; separate from `agent/` |
| `backend/knowledge_bases/` | Domain settings, prompts, policies, and shared processing paths |
| `backend/worker.py` | Background processing jobs |
| `frontend/` | React/TypeScript web application |
| `deployments/` | Docker Compose, images, and service setup |
| `benchmark/` | Evaluation inputs, labels, and scoring tools |
| `plans/` | Historical decisions and verification reports; not current implementation truth |

Paths in this table are relative to the repository root.

## Start or verify a service

- [Repository setup](../../README.md): existing backend and supporting services.
- [Agent setup](../../agent/README.md): independent install, database setup, native reload, Docker.
- [Agent API](../../agent/docs/api.md): payloads, authentication, events, and limitations.
- [Agent development](../../agent/docs/development.md): settings and unit/integration/end-to-end checks.
- [Frontend](../../frontend/README.md): browser setup, build, lint, and tests.
- [Speech service](../../deployments/speech/README.md): existing speech stack requirements.
- [Knowledge-base guide](../../backend/knowledge_bases/README.md): current domain implementation.

## Read changes in context

- [Product requirements](../features/project-overview.md) define the intended experience.
- [System responsibilities](../architecture/system-architecture.md) separate current and intended boundaries.
- [Database proposal](../architecture/database-schema.md) has tables created by
  [migrations](database-migrations.md) (`backend/src/storage/orm/`, `backend/migrations/`)
  in the `APP_DATABASE_URL` database; services do not use them yet.
- [Project guidance](../../AGENTS.md) preserves knowledge integrity and evaluation rules.

Check the current code and configuration before reporting implementation status.
Do not read private environment files into reports or treat synthetic evaluation
records as approved factory instructions.

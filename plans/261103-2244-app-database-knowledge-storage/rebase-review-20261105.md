# Knowledge-base rebase review

Completed on 2026-11-05. Branch: `feat/knowledge-base`. Base: `origin/main`
(`c3aae1d`). All seven feature commits were replayed in order. No push was made.
The original branch remains at `codex/backup-knowledge-base-before-rebase-20261105`
(`faec111`).

## Replayed commits

| Original | Rebased | Scope |
|---|---|---|
| `b5b78d6` | `9873e35` | Schema and repositories |
| `70ca9e4` | `09965fc` | Catalog, authenticated sources and APIs |
| `41b9330` | `ea84fd5` | Application ingestion worker |
| `ce4d8c8` | `7b1ce22` | Embeddings and retrieval |
| `1192d73` | `d895fbc` | KB/source management, converters and OCR |
| `14b7a4c` | `c840b45` | Search API and UI |
| `faec111` | `ed6d12f` | Conflict review and duplicate handling |

The implementation includes phases 1–4 and 6–8. The plan's older status and
legacy-storage inventory do not describe the final implementation. The dropped
phase 5 was not restored. The source pipeline, management service, repositories
and search API retain the original feature-tip logic. Trailing blank lines in
several backend files were removed to pass the full branch whitespace check.

## Resolution decisions

| Area | Preserved behavior |
|---|---|
| Chatbot | Keep `main`'s standalone `agent/`, conversation ownership/history, agent proxy, SSE completion and reasoning events, citations, photo input and speech contracts. These implementation files match `origin/main`. |
| Frontend chat | Keep `main`'s live `/ask` route, conversation page, sidebar, thinking block and file-library page/client. Retain their CSS while adding KB search styles. |
| Personal uploads | Keep `main`'s `/v1/files`: bearer auth, owner checks, content validation, size/body limits, random storage keys, attachment downloads and soft deletion that retains bytes. |
| KB uploads | Retain the feature branch's separate `/v1/documents/{kb}/uploads`, source creation, identical-file reuse, conversion/OCR, located evidence and ingestion jobs. Personal uploads do not automatically create KB sources. |
| Shared startup | Merge both sets of routes, injected services, database handlers and shutdown owners. Auth, conversations, personal files and KB repositories share one application engine. Keep the API able to start without a database URL. |
| Storage volumes | Keep `main`'s `file_library` and `document_uploads` volumes. Mount `document_uploads` in both API and worker so they read the same originals. |
| Documentation | Describe application-table use by both main's services and the KB pipeline. Keep `AGENTS.md` and `CLAUDE.md` identical. |

## Additional compatibility fixes

1. **Separate file namespaces.** Both upload flows now write `files`, but use
   different byte stores. Without filtering, `/v1/files` listed KB originals,
   attempted to download invalid library keys, and could soft-delete KB evidence.
   Restrict library reads, listings and deletion to its existing flat 32-character
   hex keys. Preserve owner checks and retain the KB source endpoints. A regression
   test reproduced the mixed listing before the fix; tests now cover access,
   deletion and both real upload paths through production startup.
2. **Isolate KB startup validation.** Invalid embedding configuration or unavailable
   KB storage must not stop main's auth, conversation and file services from
   starting. Keep those services configured and disable KB initialization when
   vector validation fails. KB routes report unavailable; correct the configuration
   and restart to enable them. Workers still require valid KB storage. Unexpected
   startup errors and cancellation still propagate, with engine cleanup.

## Verification

Tests used disposable PostgreSQL + pgvector databases on localhost:5434 and
controlled providers. Application data was not migrated, and no paid model call
was made.

| Check | Result |
|---|---|
| Backend regression: 21 relevant modules | 304 tests: 279 passed, 18 legacy tests skipped, 7 pre-existing failures |
| Original feature-tip conflict tests, extracted to `/private/tmp` | Same 7 failures among 30 tests; legacy API fixtures omit auth and receive 503 |
| Runtime subset after startup fix | 57 passed: production startup, file library, conversations, streaming and speech |
| Standalone-agent unit tests, independent lock/environment | 100 passed |
| Frontend unit tests | 18 suites and 130 tests passed; one unchanged fixture suite failed during collection because `conversation-mr-je-ax167.txt` is absent |
| Browser tests with mocked APIs | 4 passed: chat persistence/completion, failure handling, desktop/mobile conflict review |
| Frontend build and lint | Passed |
| Compose configuration | `docker compose -f deployments/docker-compose.yml config --quiet` passed |
| Migration checks | Fresh upgrade, model alignment (`alembic check`), downgrade/upgrade and existing-data tests passed in disposable databases |
| Final repository checks | `git diff --check`, `cmp AGENTS.md CLAUDE.md` and ancestry of `origin/main` passed |

Logs are under `/private/tmp/voice-agent-rebase-*`, including
`final-backend.log`, `original-conflicts.log`, `final-runtime.log`, `agent.log`,
`frontend-tests.log`, `frontend-build.log`, `frontend-lint.log` and `e2e.log`.
Resource/deprecation warnings remain in existing speech and dependency tests.
No factory-accuracy evaluation or deployment was performed.

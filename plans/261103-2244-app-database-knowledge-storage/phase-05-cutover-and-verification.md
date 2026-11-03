# Phase 5 — Cutover and verification

Depends on phases 1–4 and decisions D2 and D4. No data is imported: the new version
starts on empty application tables, and `DATABASE_URL` data stays untouched.

## Setup

- `deployments/setup.sh` / `make setup`: run `uv run alembic upgrade head` against
  `APP_DATABASE_URL` before starting the API and worker; a migration error is fatal.
- The worker no longer needs `DATABASE_URL`. Seed restore and the Industrial QA
  snapshot keep writing the legacy stores for CLI and benchmark use; their output is
  not visible in the API. State this in the setup output and in `SNAPSHOT.md`.
- `.env.example`: mark `APP_DATABASE_URL` as required for the API and worker, and
  `DATABASE_URL` as used only by CLI, benchmarks and seed restore.

## Cutover (D4)

1. Announce that existing KBs, sources, records and the `industrial_qa` QA endpoint
   disappear from the UI and API.
2. Stop API and workers. Legacy jobs still queued stay in `DATABASE_URL` unprocessed.
3. Run `uv run alembic upgrade head`, then `uv run alembic check`.
4. Start API and worker on the new code. Sign in and create the first KB.
5. Rollback: redeploy the previous image. Legacy data is unchanged; data written to the
   application tables after cutover is not visible to the previous version.

## Documentation

Update in the same change: `docs/architecture/database-schema.md` (status, `template`,
`chunk_kind`), `docs/features/knowledge-management.md`,
`docs/features/document-uploads.md`, `docs/features/voice-capture.md`,
`docs/features/authentication.md` (protected routes and roles), `docs/development/codebase-summary.md`,
`README.md` (environment), and the code boundaries in `CLAUDE.md` and `AGENTS.md`
(identical): the API serves only KBs in `APP_DATABASE_URL`; legacy stores remain for
CLI and benchmarks.

## Verification

- Fresh database: setup, sign in as `expert`, create a KB, upload a text file, extract
  it in knowledge mode and in documents mode, review a record, resolve a conflict, ask
  a question and open each cited source.
- As `employee`: upload and extract succeed; create KB and review return 403.
- Without a token: every protected route returns 401.
- Duplicates: the same file uploaded twice completes as `duplicate`; an edited copy
  waits for overlap confirmation.
- Retry: kill a worker during extraction; the job is requeued and completes without
  duplicate chunks.
- `DATABASE_URL` unset: API and worker start and serve all flows above.
- Run `uv run python -m unittest discover -s backend/tests -t . -v`; separate known
  failures (missing KB packages, stale approval assertions) from regressions.
- Frontend `npm run build`, `npm run lint`, touched tests.
- `git diff --check` and `cmp AGENTS.md CLAUDE.md`.

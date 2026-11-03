# Phase 5 — Integration and verification

Add cross-service verification cases to the existing agent end-to-end suite and
frontend service tests as their boundaries require. Keep the live teammate API
checks as release gates. Record each gate as passed, failed, or blocked, with the
command or fixture and the observed result. Controlled search results establish
tool behavior. The live known-fault fixture establishes evidence link behavior.

- [ ] Run agent unit tests and restart/checkpoint integration tests per `agent/docs/development.md`. Use a controlled provider and disposable PostgreSQL.
- [ ] Run targeted backend unit and integration checks from the repository root using `uv`. Apply Alembic migrations to a disposable app database and verify runtime access via `APP_DATABASE_URL`. Preserve developer databases and volumes.
- [ ] Run frontend targeted tests, `npm run lint`, and `npm run build` from `frontend/`.
- [ ] Verify the native CLI and container paths affected by changes. Use real HTTP to check SSE buffering, terminal events, disconnect cleanup, and independent conversations.
- [ ] With real login and file APIs, verify create → text or transcript → stream → restart → reopen. Verify the owner can send a photo and a second user cannot use its ID.
- [ ] With the real knowledge API, ingest a known fixture and open the correct citation from both the answer and reopened history. Verify empty and unavailable retrieval have distinct outcomes.
- [ ] With the real inquiry API, draft → edit → submit → clarify → reply → accept. Verify the expert can read the snapshot with the agent offline and acceptance creates zero knowledge chunks.
- [ ] Update agent API docs, frontend/backend setup instructions, and relevant architecture docs with actual limits and prerequisites. Run `git diff --check` and verify `AGENTS.md` and `CLAUDE.md` match.
- [ ] Report actual checks and remaining integration gaps. Use explicit opt-in for paid provider checks.

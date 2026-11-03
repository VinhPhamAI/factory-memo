# Phase 4 — Browser and release verification

Add a /auth proxy alongside the existing /v1 proxy in
`frontend/vite.config.ts`. In deployment, route /auth and /v1 to the backend
under the frontend origin. Check auth write origins against configured frontend
origins, including the dev origin when explicitly enabled, rather than the
upstream Host rewritten by the proxy.

Refreshing replaces the prior access token for the browser session. Coordinate
refresh and logout across same-origin tabs with a browser lock where supported.
Broadcast token changes in memory to active tabs and clear them on logout.
Never persist credentials in browser storage. On browsers without cross-tab
coordination, keep refresh single-flight per tab and require sign-in when a
stale token fails. Avoid automatic refresh loops after an unexpected 401.
Test two tabs sharing one cookie, including that a rejected refresh leaves the
winning cookie intact. Verify Compose supplies the runtime DB URL and the
deployed origin configuration.

- **Create `frontend/src/services/auth-api.ts`.** Implement signup, login, refresh, me, logout and access-token attachment. Keep access in memory. Coalesce refresh calls per tab. Use same-origin requests and configure the development proxy in `frontend/vite.config.ts` if needed. A losing refresh from another tab may require that tab to sign in again. It must not erase the shared cookie.
- **Create `frontend/src/hooks/auth.tsx` and wire in `frontend/src/main.tsx`.** Restore identity on reload, expose session state, and clear it after logout or refresh failure. Add a focused signup/login entry UI and gate protected screens. Never automatically resend a chat turn.
- **First failing tests.** Add `frontend/src/services/auth-api.test.ts` for memory-only access, one refresh per tab, expiry, and failed refresh. Add a browser smoke test for signup → login → me → refresh → logout → 401 with the same last access token.
- **Documentation.** Update the migration guide, backend auth API docs and frontend setup docs. Record development proxy and deployed cookie/origin settings, API examples, token lifetimes, provisioning, and upgrade behavior.
- **Release gates.** Run backend unit and PostgreSQL integration tests, migration setup and schema check, frontend tests, lint and build, then inspect the scoped diff and run git diff --check. Verify actual browser cookie behavior in the deployed topology. Report failures and skips. Confirm branch and review results before committing and pushing. Preserve existing plans and unrelated changes.

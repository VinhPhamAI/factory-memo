# Docs update: database setup and authentication

## Files changed
- Created `docs/features/authentication.md`: `/auth` contract (endpoints, validation, errors, sessions/tokens, passwords, accounts/roles, concurrency rules, `require_current_user`, settings, deployment, curl examples, Windows loop limitation).
- `docs/development/database-migrations.md`: sixteen tables (fifteen proposal tables + `auth_sessions`); `users`/`auth_sessions` used by the API; two new revisions in the (renamed) "Revisions" table; runtime vs setup URL roles; "Upgrade existing databases" subsection.
- `frontend/README.md`: sign-in required in api mode (mock needs none); `/auth` proxied with `/v1`; `AUTH_ALLOWED_ORIGINS` for local dev; Windows `--loop` pointer; same-origin `/auth` in production; new "Sign-in" section (memory-only access token, cookie restore, Web Locks + BroadcastChannel); auth Playwright smoke test.
- `README.md`: one pointer in section 3 to the auth doc and its env vars.
- `docs/README.md`: Features index entry; schema-proposal note no longer says "not yet used by services".

## Evidence
Source: backend/api/auth.py, auth_schemas.py, services/auth.py, storage/auth.py, storage/app_database.py, app.py, docker-compose.yml, .env.example, both new revisions, SessionStorageUpgradeTest, ORM metadata (16 tables), frontend auth-api.ts, hooks/auth.tsx, main.tsx, vite.config.ts, playwright.config.ts, e2e/auth.spec.ts. uvicorn 0.54 `--loop` accepts an import string. curl 8.16 sends Secure cookies to http loopback (tested locally against a stub server).

## Validation
Relative links and anchors resolved by script; `git diff --check` clean. Live auth endpoints not exercised (no backend running).

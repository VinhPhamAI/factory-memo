# Code review: database and authentication (uncommitted)

Date: 2026-11-03. Scope: backend auth API/service/storage, app database, ORM, two
migrations, Compose/env, backend tests, frontend auth client/hook/UI, Vite/Playwright
config and e2e specs. Requirements: `plans/261103-1538-database-auth/`.

## Score: 8/10

The core design is sound and matches the plan: opaque 32-byte tokens, SHA-256
digests only, Argon2id with dummy hash, access capped at the fixed 72 h deadline via
`least()`, rotation as a conditional `UPDATE ... RETURNING` after the account-row lock,
logout by session ID, conflict → 400, no Set-Cookie on failed refresh, 503 mapping
that logs only the exception class. No critical issues found. One high-severity input
gap and a logout UX/security mismatch need fixing before release.

## Verification run

| Check | Result |
|---|---|
| `unittest backend.tests.test_auth test_app_database test_database_migrations` (offline) | 55 run, 41 skipped, OK |
| Same, with `TEST_DATABASE_URL` → local `denso-auth-test-pg` container | 52 run, OK (includes concurrency races) |
| `npm run test -- src/services/auth-api.test.ts` | 8 passed |
| `npm run lint`, `tsc -b --noEmit` | clean |
| `npm run build`, Playwright auth suite | not run (read-only review; build writes `dist/`) |

## Critical

None.

## High

### H1. NUL bytes reach PostgreSQL: unauthenticated 500 and password hash written to error logs
- `backend/api/auth_schemas.py:22` (`DisplayName`), `:46-47` (`LoginRequest`), `backend/src/storage/auth.py:117-124`, `backend/src/storage/app_database.py:41`.
- `DisplayName` and the login username accept `\u0000` (verified: `SignupRequest(display_name="A\x00", ...)` validates). psycopg raises `DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes` at bind time. `_transaction` maps only `OperationalError/InterfaceError/OSError`; `create_employee` catches only `IntegrityError`. Result: 500, and Starlette logs the traceback whose SQLAlchemy message includes `[parameters: {... 'password_hash': '$argon2id$...'}]` (verified against the test container).
- Scenario: `POST /auth/signup {"username":"abc","display_name":"A\u0000","password":"<15+ chars>"}` → 500 plus the new user's Argon2 hash in server logs. `POST /auth/login {"username":"a\u0000b",...}` → 500 instead of the generic 401.
- Fix: reject control characters (at least `\x00`) in `DisplayName`, `SignupPassword`-irrelevant, and `LoginRequest.username` (422 for signup; login may map to 401 via `normalize_username` raising `ValueError`). Also pass `hide_parameters=True` to `create_async_engine` so no future DB error logs credential material. Add a test for both endpoints.

## Warnings

### W1. Failed logout signs the tab out locally but leaves a live session and cookie
- `frontend/src/services/auth-api.ts:151-167` (comment at `:162`), test `auth-api.test.ts:137`.
- `logout()` ignores the response status and swallows network errors, then signs out and broadcasts. On network failure, 503 (DB down) or 400 (conflict), the server session is not revoked and the HttpOnly cookie is not cleared. The comment "the server session expires on its own" is wrong: the next page load calls `restore()` → `/auth/refresh` and silently signs the user back in for up to 72 h.
- Second-order race: after the local sign-out broadcast, another tab whose timer refresh was queued on the Web Lock can refresh with the surviving cookie and broadcast `tokens`, re-signing every tab in.
- Scenario: shared factory terminal, Wi-Fi blip or DB restart during "Đăng xuất"; the next worker opens the app and is signed in as the previous user.
- Fix: check `response.ok` (204) before reporting success; on failure keep or clearly flag the state and tell the user logout did not complete (allow retry). Correct the comment and the test name.

### W2. Pool exhaustion and lock waits are not bounded or mapped to 503
- `backend/src/storage/auth.py:89-93`, `backend/src/storage/app_database.py:41`.
- `sqlalchemy.exc.TimeoutError` (QueuePool overflow, default 5+10 connections, 30 s wait) is not an `OperationalError` → 500 instead of the agreed 503. There is no `statement_timeout`/`lock_timeout`, so a long transaction holding a `users` row lock (manual SQL, future admin op) blocks login/refresh/logout for that account indefinitely while consuming pool connections.
- Scenario: a burst of logins/refreshes during a slow DB → requests hang 30 s then 500; a stuck maintenance transaction on one user row ties up workers.
- Fix: add `sqlalchemy.exc.TimeoutError` to the mapped exceptions; set `connect_args={"options": "-c lock_timeout=5s -c statement_timeout=10s"}` (values to choose) so lock waits fail as `OperationalError` → 503.

### W3. `AUTH_INSECURE_LOOPBACK_COOKIES` passes validation with an empty origin list
- `backend/api/auth.py:69-75`. `all(...)` over an empty set is `True`.
- Scenario: deployment sets `AUTH_INSECURE_LOOPBACK_COOKIES=true` and forgets `AUTH_ALLOWED_ORIGINS`; startup succeeds and refresh cookies are issued without `Secure` to non-browser clients over plain HTTP on a non-loopback host. Browsers are blocked anyway (403), so impact is limited, but the "loopback-only" guarantee the plan requires does not hold.
- Fix: require a non-empty origin set when insecure cookies are enabled; add the case to `CookieSettingsTest`.

### W4. Logout conflict decision is made before the account lock
- `backend/src/storage/auth.py:262-279`. Both lookups and the conflict check run before `_lock_account`; only the revoke runs under the lock. The phase plan says "recheck the supplied credential and account state after acquiring the lock."
- Scenario: tab A logs out session S2 while the same browser sends logout with cookie S1 and stale bearer S2; the pre-lock snapshot still sees S2 live → 400 although S2 is revoked a moment later. Outcome is a spurious 400, not data corruption; the agreed "logout wins by session ID" behavior is preserved.
- Fix (low cost): after locking, re-run the two lookups (or lock first by resolving the user from either digest) and decide conflict on the locked state.

### W5. Validation errors echo the submitted password
- `backend/api/auth_schemas.py:24`, `:47`; FastAPI default handler. Verified: `POST /auth/signup` with a 12-char password returns `{"detail":[{..., "input": "short-secret", ...}]}`.
- Scenario: any proxy, APM or browser-extension logging response bodies records plaintext passwords from failed signups/logins (too long/short).
- Fix: a `RequestValidationError` handler for `/auth/*` that drops `input` (and `ctx` for password fields), or mark the password fields with `SecretStr`. Pin the 422 shape in a test as the plan asks ("pin the final shapes in tests").

## Minor

1. `backend/migrations/versions/f841068e1683_add_auth_sessions.py:22-39`: no cleanup path or `expires_at` index; every login adds a row that is never deleted. Fine for the hackathon, but record it as a known limitation or add a periodic `DELETE ... WHERE expires_at < now() - interval '30 days'`.
2. `frontend/src/services/auth-api.ts:265-270`: near the 72 h deadline the timer refreshes at half the remaining lifetime repeatedly (about log2(remaining s) rotations in the last minute) and ends with a 401 sign-out. Consider stopping scheduled refresh when `expires_in` stops growing and prompting re-login.
3. `frontend/src/services/auth-api.ts:193`: every refresh failure (network error, 503) signs the tab out even though the cookie is still valid. Acceptable per plan ("avoid refresh loops"), but a 503 message instead of the sign-in screen would be less confusing; reload recovers.
4. `frontend/src/services/auth-api.ts:122-127`: if `/auth/me` fails after a successful login, the server session and cookie exist but the UI reports an error; a reload signs the user in. Low impact.
5. `backend/src/services/auth.py:236-243`: no `check_needs_rehash`; future parameter changes will not upgrade stored hashes. Hashes with non-default Argon2 parameters also make the dummy-hash timing slightly different from real accounts.
6. `frontend/src/services/auth-api.ts:156-158`: `credentials: "same-origin"` means a deployment that sets `VITE_API_BASE_URL` to a different origin silently loses the cookie. The plan mandates same-origin routing; state it next to `apiBaseUrl` in `config.ts` or fail fast when it is cross-origin in live mode.
7. Without Web Locks (non-secure, non-loopback HTTP), two tabs refreshing together: the loser's 401 can arrive after the winner's broadcast and sign the loser out. Matches the plan's accepted degradation; noted for completeness.
8. Existing `/v1` routes are still unauthenticated; `RequireAuth` is UI gating only and `authorizedFetch` has no callers. This matches the plan ("future protected routes"), but the release notes should say so.

## Plan compliance

| Requirement | Status |
|---|---|
| Opaque tokens, 15 min access capped at fixed 72 h deadline, actual `expires_in` | Done (`services/auth.py:96-102`, `storage/auth.py:237`) |
| Cookie HttpOnly/Secure/SameSite=Strict/Path=/auth; delete with same scope | Done (`api/auth.py:150-161`, `:243-249`) |
| Digests only, Argon2id, dummy hash, hashing off the event loop | Done |
| Generic 401; signup employee, extra fields 422, used username 409 incl. deleted | Done (tests cover) |
| Refresh atomic replace, reuse → 401 without Set-Cookie | Done (tests incl. two-connection race) |
| Logout single session, repeat 204, conflict 400 | Done; see W4 for pre-lock decision |
| All session mutations lock `users` row first | Done for login/refresh/logout; no disable op exists, documented requirement |
| 503 unconfigured/unreachable | Done, except pool timeout (W2) |
| Origin check on auth writes | Done; no-Origin allowed by agreement |
| Frontend memory-only token, per-tab coalescing, Web Lock + BroadcastChannel, no chat resend | Done; logout failure handling (W1) |
| Migrations: autogenerated table + hand-written trigger revision, upgrade preserves users | Done; tests pass on PostgreSQL |

## Recommended actions

1. H1: reject NUL/control characters in auth schemas; enable `hide_parameters=True`; add tests.
2. W1: make frontend logout success depend on a 204; fix the misleading comment/test.
3. W2: map pool timeouts to 503; set lock/statement timeouts.
4. W3, W5: tighten insecure-cookie validation; strip `input` from auth 422 bodies and pin the shape.
5. Optional: W4 lock-first logout; session cleanup.

## Unresolved questions

- Is the shared-terminal scenario (W1) in scope for the demo? It determines whether W1 blocks release.
- Production frontend topology (same-origin reverse proxy) is not in Compose; the "verify deployed cookie behavior" release gate remains open.

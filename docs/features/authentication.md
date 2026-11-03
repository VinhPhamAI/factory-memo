# Authentication

Scope: the implemented `/auth` API for employee signup, password login and
device sessions. Accounts and sessions live in the application database
(`users`, `auth_sessions`; see [database migrations](../development/database-migrations.md)).
[Agent conversations](conversations.md) (`/v1/conversations`) require a bearer access
token through `require_current_user`; other `/v1/...` routes do not yet. The frontend
requires sign-in before showing API-mode screens ([frontend guide](../../frontend/README.md)).

Owners: routes in `backend/api/auth.py`, bodies in `backend/api/auth_schemas.py`,
rules in `backend/src/services/auth.py`, queries in `backend/src/storage/auth.py`,
tests in `backend/tests/test_auth.py`.

## Endpoints

| Method and path | Credential | Success |
|---|---|---|
| `POST /auth/signup` | None | `201` `{id, username, display_name, role}` |
| `POST /auth/login` | None | `200` `{access_token, token_type: "bearer", expires_in}` and a refresh cookie |
| `GET /auth/me` | `Authorization: Bearer <access_token>` | `200` `{id, username, display_name, role}` |
| `POST /auth/refresh` | Refresh cookie | `200` same token fields and a rotated refresh cookie |
| `POST /auth/logout` | Refresh cookie and/or bearer | `204` and a cleared refresh cookie |

Token responses and `/auth/me` send `Cache-Control: no-store`. The refresh cookie
is `refresh_token`: `HttpOnly`, `SameSite=Strict`, `Path=/auth` and `Secure`
(unless loopback insecure cookies are enabled, see [Settings](#settings)).

## Request validation

Bodies reject unknown fields with `422`. Validation errors on `/auth` list each
field's `type`, `loc` and `msg` but never echo submitted values such as passwords.

| Field | Rule |
|---|---|
| Signup `username` | Trimmed and lowercased, then 3–32 characters of `a-z 0-9 _ -` |
| Signup `display_name` | Trimmed, 1–80 characters, no control characters (line breaks, NUL) |
| Signup `password` | 15–128 characters, stored exactly as typed (spaces kept) |
| Login `username` | 1–256 characters; normalized before lookup; control characters fail with `401` |
| Login `password` | 1–1024 characters |

## Errors

Error bodies have the shape `{"detail": ...}`.

| Status | Cause |
|---|---|
| `422` | Malformed body, out-of-range field or unknown field |
| `409` | Signup username was ever used, including by a soft-deleted account |
| `401` | Any login failure (one generic message); missing, unknown, expired or revoked credential; ineligible account |
| `400` | Logout where the bearer and the cookie name different live sessions; neither is revoked |
| `403` | Browser `Origin` not in `AUTH_ALLOWED_ORIGINS` (signup, login, refresh, logout) |
| `503` | `APP_DATABASE_URL` unset (auth not configured), the database is unreachable, or a connection, row-lock (5 s) or statement (10 s) wait timed out; no connection details are returned |

## Sessions and tokens

- Each login creates a new session; one account can hold sessions on several devices.
- Access and refresh tokens are independent opaque random strings. Only their
  SHA-256 digests are stored in `auth_sessions`.
- Access tokens last 15 minutes. Each session has a fixed 72-hour deadline from
  login; refresh never extends it and access expiry is capped at it.
- `expires_in` is the actual remaining access lifetime in seconds. The cookie
  `Max-Age` is the remaining session time.
- Refresh rotates both tokens. Reusing an already rotated refresh token returns
  `401` with no `Set-Cookie`, so a tab that lost a refresh race does not erase the
  cookie another tab just received. Only one of several concurrent refreshes wins.
- Logout prefers a live refresh cookie, otherwise uses the bearer token. Logout
  without a usable credential, or a repeated logout, still returns `204`. A logout
  racing a refresh of the same session still revokes it.
- The browser treats only `204` as signed out. After a network error or another
  status it stays signed in and asks the user to retry, because the session and
  refresh cookie would otherwise survive unseen.

## Passwords

Passwords are hashed with Argon2id (`argon2-cffi` defaults). A stored hash in any
other format fails login and logs a warning without the account details. Migrations
do not convert stored hashes.

## Accounts and roles

- Signup creates only active `employee` accounts; the role cannot be chosen.
- Experts and admins are provisioned separately, for example by an administrator
  directly in the database. No endpoint creates or changes roles.
- `is_active` and `deleted_at IS NULL` are checked on every login, refresh and
  authenticated request. The role is read from the database per request.

## Concurrency rules for account maintenance

Login, refresh and logout lock the account's `users` row before changing a session.
Any future disable or soft-delete operation, including out-of-band maintenance,
must follow the same rules:

1. Lock the `users` row first.
2. Revoke the account's sessions (`revoked_at`) in the same transaction.
3. When re-enabling an account, leave revoked sessions revoked.

## Protecting routes

New protected routes depend on `require_current_user` in `backend/api/auth.py`.
It accepts only a bearer access token (not the cookie), returns the account with
its current role, and fails with `401` and `WWW-Authenticate: Bearer`.

## Settings

| Variable | Use |
|---|---|
| `APP_DATABASE_URL` | Runtime database for accounts and sessions. Unset: auth routes return `503` |
| `AUTH_ALLOWED_ORIGINS` | Comma-separated frontend origins, compared with the browser `Origin` header (not `Host`). Requests without `Origin`, such as `curl`, are allowed |
| `AUTH_INSECURE_LOOPBACK_COOKIES` | `true` drops the cookie `Secure` flag; startup fails unless every allowed origin is loopback |

`MIGRATION_DATABASE_URL` is for Alembic only and is never read by the API.

## Deployment

1. Apply migrations to the application database
   ([database migrations](../development/database-migrations.md#apply-migrations)).
2. Serve the frontend and backend from the same origin: reverse-proxy `/auth` and
   `/v1` to the backend. The cookie is scoped to `/auth` and `SameSite=Strict`.
3. Set `AUTH_ALLOWED_ORIGINS` to the deployed frontend origin.

Docker Compose passes `APP_DATABASE_URL` to the API from `API_APP_DATABASE_URL`,
defaulting to the `voice_agent_app` database on `postgres:5432`
(`deployments/docker-compose.yml`).

## Examples

Run against a local backend; replace the example password.

```bash
BASE=http://127.0.0.1:8005
curl -i -X POST $BASE/auth/signup -H 'Content-Type: application/json' \
  -d '{"username":"an_nguyen","display_name":"An Nguyen","password":"example-password-123"}'
curl -s -c jar.txt -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"an_nguyen","password":"example-password-123"}'
# Copy access_token from the login response:
curl -s $BASE/auth/me -H "Authorization: Bearer $ACCESS_TOKEN"
curl -s -b jar.txt -c jar.txt -X POST $BASE/auth/refresh
curl -i -b jar.txt -c jar.txt -X POST $BASE/auth/logout
```

The cookie jar holds a live refresh token; delete `jar.txt` afterwards.

## Limitations

- On a native Windows host, uvicorn's default Proactor event loop is rejected by
  async psycopg, so auth and knowledge storage return errors. Start the API with:
  `uv run uvicorn backend.app:app --host 127.0.0.1 --port 8005 --loop asyncio:SelectorEventLoop`.
- There is no password change, password reset, account disable or role management
  endpoint.

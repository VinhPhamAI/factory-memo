# Database and authentication

Status: implemented and verified (uncommitted). Scope: section 1, including employee signup and session refresh.

## Execution order

1. [Application database](phase-01-database.md)
2. [Accounts and login](phase-02-accounts.md)
3. [Identity, rotation, and logout](phase-03-sessions.md)
4. [Browser integration and release checks](phase-04-verification.md)

Build each slice test-first. Release the browser flow together with refresh, identity, and revocation. Follow [engineering standards](../../docs/development/code-standards.md) and [migration instructions](../../docs/development/database-migrations.md). Paths in phases are repository-relative and sketches show proposed boundaries.

## Contract

| Endpoint | Input | Success |
|---|---|---|
| POST /auth/signup | JSON username, display_name, password | 201 with id, username, display_name, role |
| POST /auth/login | JSON username, password | 200 with access_token, token_type bearer, expires_in (normally 900), and refresh cookie |
| GET /auth/me | Bearer access token | 200 with id, username, display_name, role |
| POST /auth/refresh | Refresh cookie | 200 with the login token fields and rotated cookie |
| POST /auth/logout | Current bearer or refresh cookie | 204 and clear cookie |

- Use opaque tokens. Access expires in 15 minutes or at the session deadline, whichever comes first. Each login has a fixed 72-hour deadline. Return the actual remaining access lifetime in expires_in.
- Return 422 for malformed payloads, 409 for reserved usernames, and a generic 401 for invalid credentials or unusable tokens. Logout without a usable credential returns 204. Protected routes accept only bearer access tokens. Use the existing error envelope if applicable and pin the final shapes in tests.
- Signup creates an active employee. Experts and admins are provisioned separately. Multiple sessions are allowed. Logout revokes one session. Disable or soft-delete invalidates all sessions.
- Use same-origin browser requests and reject disallowed origins on auth writes. Serve deployment cookies with Secure, HttpOnly, SameSite=Strict, and Path=/auth. Use an explicit loopback-only local development configuration if needed.
- Legacy knowledge storage remains in place. Preserve existing user IDs, roles, and hashes.
- Before implementation, check for overlapping auth work and agree on the target branch.

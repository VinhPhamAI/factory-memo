# Phase 3 — Identity, refresh, and logout

- **Edit `backend/src/services/auth.py`.** Add `authenticate_access`, `refresh_session`, and `logout`. Inject the clock for tests. Resolve user role and eligibility for every protected request. Use independent cryptographically random access and refresh secrets.
- **Edit `backend/src/storage/auth.py`.** Look up the active access digest. For refresh, use an atomic conditional update matching the presented digest, a live session and an eligible account. Replace both digests and cap access expiry at the session deadline. A losing refresh returns 401 and sends no cookie change. Reuse of the old refresh token fails. Revoke sessions permanently when disabling an account so re-enabling does not revive them.
- **Edit `backend/api/auth.py`.** Expose `require_current_user` for future protected routes and register /me, /refresh, /logout. On logout prefer the presented refresh cookie if valid, otherwise use the bearer session. If both identify different live sessions, return 400 and leave both untouched. Return 204 for repeated logout and clear the refresh cookie using the original scope.
- **First failing tests.** Extend `backend/tests/test_auth.py` to prove same-token access fails after logout, current role is read, expired access can refresh before the deadline, and refresh cannot extend the deadline. Test reuse, concurrent rotation with two database sessions, inactive and soft-deleted users, and independent device logout.

Illustrative storage boundary. The repository performs the compare-and-replace as one transaction.

Serialize session mutations for the same account by acquiring the user row
before touching its sessions. Login, refresh, logout, and account disablement
must use the same lock order. Recheck the supplied credential and account state
after acquiring the lock. Add a refresh-versus-logout test and a disable-versus-login
test with explicit coordination. Treat logout as revoking the session if its
credential wins the race. Browser auth writes should wait for any refresh
already in flight in the same tab before sending logout.

Account-management endpoints are outside this phase. Document that any future
disable or soft-delete operation must revoke sessions in the same transaction.
Cover the current eligibility check with disabled and deleted rows and document
the re-enable requirement for any out-of-band maintenance procedure.

```python
async def refresh_session(cookie: str, store: SessionStore, now: datetime) -> TokenPair:
    """Rotate a live session and return its new access and refresh credentials."""
    tokens = TokenPair.generate()
    expiry = await store.rotate_if_live(digest(cookie), tokens.digests(), now)
    if expiry is None:
        raise InvalidSession()
    return tokens.with_access_expiry(expiry)
```

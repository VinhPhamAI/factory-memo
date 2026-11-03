"""Employee signup, password login and opaque bearer sessions.

Access and refresh credentials are independent random strings; only their
SHA-256 digests are stored. Access lasts up to ACCESS_TOKEN_LIFETIME and never
beyond the session deadline, which is fixed SESSION_LIFETIME after login.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from backend.src.storage.auth import (
    Account,
    AuthStorageUnavailable,
    AuthStore,
    LogoutOutcome,
    SessionCredentials,
    UsernameTakenError,
)
from backend.src.storage.orm import normalize_username


__all__ = [
    "ACCESS_TOKEN_LIFETIME",
    "SESSION_LIFETIME",
    "Account",
    "AuthService",
    "AuthStorageUnavailable",
    "ConflictingCredentials",
    "InvalidCredentials",
    "InvalidSession",
    "IssuedTokens",
    "UsernameTakenError",
    "token_digest",
]

ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(hours=72)
TOKEN_BYTES = 32

logger = logging.getLogger(__name__)


class InvalidCredentials(ValueError):
    """The username and password do not identify an eligible account."""


class InvalidSession(ValueError):
    """The presented credential does not identify a live session."""


class ConflictingCredentials(ValueError):
    """The refresh and access credentials identify different live sessions."""


@dataclass(frozen=True)
class IssuedTokens:
    """Credentials returned to the client after login or refresh.

    Attributes:
        access_token: Bearer credential for protected routes.
        refresh_token: Credential sent only in the refresh cookie.
        expires_in: Seconds until the access credential expires.
        refresh_expires_in: Seconds until the session deadline.
    """

    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int


def token_digest(token: str) -> str:
    """Return the lowercase hexadecimal SHA-256 digest stored for a credential."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _seconds_until(moment: datetime, now: datetime) -> int:
    return max(0, int((moment - now) / timedelta(seconds=1)))


def _new_credentials(now: datetime) -> tuple[str, str, SessionCredentials]:
    access, refresh = secrets.token_urlsafe(TOKEN_BYTES), secrets.token_urlsafe(TOKEN_BYTES)
    return access, refresh, SessionCredentials(
        access_token_digest=token_digest(access),
        refresh_token_digest=token_digest(refresh),
        access_expires_at=now + ACCESS_TOKEN_LIFETIME,
    )


class AuthService:
    """Applies account and session rules on top of an AuthStore.

    Args:
        store: Account and session storage.
        clock: Returns the current UTC time; replaced in tests.
        password_hasher: Argon2id hasher; tests may pass cheaper parameters.
    """

    def __init__(
        self,
        store: AuthStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._hasher = password_hasher or PasswordHasher()
        # Unknown usernames are checked against this hash so they take as long
        # as wrong passwords.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(TOKEN_BYTES))

    async def signup(self, username: str, display_name: str, password: str) -> Account:
        """Create an active employee account.

        Args:
            username: Validated, normalized username.
            display_name: Validated display name.
            password: Password exactly as entered.

        Returns:
            The new account.

        Raises:
            UsernameTakenError: The username is already in use or was used before.
            AuthStorageUnavailable: The database could not be reached.
        """

        password_hash = await asyncio.to_thread(self._hasher.hash, password)
        return await self._store.create_employee(username, display_name, password_hash)

    async def login(self, username: str, password: str) -> IssuedTokens:
        """Verify a password and start a new session for the account.

        Raises:
            InvalidCredentials: Unknown username, wrong password, unsupported
                stored hash, or a disabled or deleted account.
            AuthStorageUnavailable: The database could not be reached.
        """

        try:
            normalized = normalize_username(username)
        except ValueError as exc:
            raise InvalidCredentials() from exc
        # No stored name contains control characters, and PostgreSQL rejects NUL.
        if any(unicodedata.category(character) == "Cc" for character in normalized):
            raise InvalidCredentials()
        candidate = await self._store.find_login_candidate(normalized)
        stored_hash = candidate.password_hash if candidate else self._dummy_hash
        if not await asyncio.to_thread(self._password_matches, stored_hash, password):
            raise InvalidCredentials()
        if candidate is None or not candidate.eligible:
            raise InvalidCredentials()

        now = self._clock()
        access, refresh, credentials = _new_credentials(now)
        expires_at = now + SESSION_LIFETIME
        if not await self._store.start_session(candidate, credentials, expires_at):
            raise InvalidCredentials()
        return IssuedTokens(
            access_token=access,
            refresh_token=refresh,
            expires_in=_seconds_until(credentials.access_expires_at, now),
            refresh_expires_in=_seconds_until(expires_at, now),
        )

    async def authenticate_access(self, access_token: str) -> Account:
        """Resolve a bearer access credential to its current account.

        The role and eligibility are read from the database on every call.

        Raises:
            InvalidSession: The credential is unknown, expired or revoked, or
                the account is disabled or deleted.
            AuthStorageUnavailable: The database could not be reached.
        """

        account = await self._store.find_account_by_access(token_digest(access_token), self._clock())
        if account is None:
            raise InvalidSession()
        return account

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        """Rotate both credentials of a live session without extending its deadline.

        Raises:
            InvalidSession: The refresh credential is unknown, already used,
                revoked or past the session deadline, or the account is no
                longer eligible.
            AuthStorageUnavailable: The database could not be reached.
        """

        now = self._clock()
        access, refresh, credentials = _new_credentials(now)
        rotated = await self._store.rotate_session(token_digest(refresh_token), credentials, now)
        if rotated is None:
            raise InvalidSession()
        access_expires_at, expires_at = rotated
        return IssuedTokens(
            access_token=access,
            refresh_token=refresh,
            expires_in=_seconds_until(access_expires_at, now),
            refresh_expires_in=_seconds_until(expires_at, now),
        )

    async def logout(self, refresh_token: str | None, access_token: str | None) -> None:
        """Revoke the session named by the refresh cookie or the bearer credential.

        Logging out with no usable credential succeeds without changes.

        Raises:
            ConflictingCredentials: The two credentials identify different live sessions.
            AuthStorageUnavailable: The database could not be reached.
        """

        outcome = await self._store.revoke_session(
            token_digest(refresh_token) if refresh_token else None,
            token_digest(access_token) if access_token else None,
            self._clock(),
        )
        if outcome is LogoutOutcome.CONFLICT:
            raise ConflictingCredentials()

    def _password_matches(self, stored_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(stored_hash, password)
        except InvalidHashError:
            logger.warning("An account has a password hash in an unsupported format")
            return False
        except VerificationError:
            return False

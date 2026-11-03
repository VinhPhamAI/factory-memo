"""Account and login session queries in the application database.

Every session change (login, refresh, logout and any future account
disablement) first locks the account's `users` row, then rechecks the
credential and account state. This one lock order serializes concurrent
changes for an account without deadlocks.
"""

from __future__ import annotations

import enum
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.storage.orm import AuthSession, User, UserRole


USERNAME_UNIQUE_CONSTRAINT = "uq_users_username"

# Disabled or soft-deleted accounts cannot sign in or use existing sessions.
ACCOUNT_ELIGIBLE = and_(User.is_active.is_(True), User.deleted_at.is_(None))


class AuthStorageUnavailable(RuntimeError):
    """The application database could not be reached."""


class UsernameTakenError(ValueError):
    """The username belongs to another account, including a deleted one."""


@dataclass(frozen=True)
class Account:
    """Public fields of an account."""

    id: UUID
    username: str
    display_name: str
    role: str


@dataclass(frozen=True)
class LoginCandidate:
    """Stored credentials of the account a login names."""

    id: UUID
    password_hash: str
    eligible: bool


@dataclass(frozen=True)
class SessionCredentials:
    """Digests and expiry times written for a new or rotated session."""

    access_token_digest: str
    refresh_token_digest: str
    access_expires_at: datetime


class LogoutOutcome(enum.Enum):
    """Result of a logout request."""

    REVOKED = "revoked"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"


def _account(user: User) -> Account:
    return Account(id=user.id, username=user.username, display_name=user.display_name, role=user.role)


class AuthStore:
    """Reads and writes accounts and sessions through short-lived transactions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        """Open one transaction; connection failures become AuthStorageUnavailable."""

        try:
            async with self._sessions() as session, session.begin():
                yield session
        # Includes lock and statement timeouts (OperationalError) and pool exhaustion.
        except (OperationalError, InterfaceError, PoolTimeoutError, OSError) as exc:
            raise AuthStorageUnavailable("The application database is unavailable") from exc

    async def create_employee(self, username: str, display_name: str, password_hash: str) -> Account:
        """Insert an active employee account.

        Args:
            username: Normalized username.
            display_name: Name shown to other users.
            password_hash: Encoded password hash.

        Returns:
            The created account.

        Raises:
            UsernameTakenError: Another account already uses the username.
            AuthStorageUnavailable: The database could not be reached.
        """

        user = User(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            role=UserRole.EMPLOYEE.value,
        )
        try:
            async with self._transaction() as session:
                session.add(user)
        except IntegrityError as exc:
            # Only the username constraint means "taken"; other violations are bugs.
            if getattr(exc.orig.diag, "constraint_name", None) == USERNAME_UNIQUE_CONSTRAINT:
                raise UsernameTakenError(username) from exc
            raise
        return _account(user)

    async def find_login_candidate(self, username: str) -> LoginCandidate | None:
        """Look up the credentials of a normalized username, or None if unknown."""

        async with self._transaction() as session:
            row = (
                await session.execute(
                    select(User.id, User.password_hash, ACCOUNT_ELIGIBLE).where(User.username == username)
                )
            ).one_or_none()
        if row is None:
            return None
        return LoginCandidate(id=row[0], password_hash=row[1], eligible=bool(row[2]))

    async def start_session(
        self,
        candidate: LoginCandidate,
        credentials: SessionCredentials,
        expires_at: datetime,
    ) -> bool:
        """Create a session if the account is still eligible with the same password.

        Args:
            candidate: Account whose password the caller verified.
            credentials: Digests and access expiry of the new session.
            expires_at: Fixed session deadline.

        Returns:
            True when the session was created; False when the account was
            disabled, deleted or given a new password since it was read.
        """

        async with self._transaction() as session:
            await _lock_account(session, candidate.id)
            still_valid = await session.scalar(
                select(User.id).where(
                    User.id == candidate.id,
                    User.password_hash == candidate.password_hash,
                    ACCOUNT_ELIGIBLE,
                )
            )
            if still_valid is None:
                return False
            session.add(
                AuthSession(
                    user_id=candidate.id,
                    access_token_digest=credentials.access_token_digest,
                    refresh_token_digest=credentials.refresh_token_digest,
                    access_expires_at=credentials.access_expires_at,
                    expires_at=expires_at,
                )
            )
        return True

    async def find_account_by_access(self, access_token_digest: str, now: datetime) -> Account | None:
        """Return the eligible account of a live, unexpired access credential."""

        async with self._transaction() as session:
            user = await session.scalar(
                select(User)
                .join(AuthSession, AuthSession.user_id == User.id)
                .where(
                    AuthSession.access_token_digest == access_token_digest,
                    *_live_session(now),
                    AuthSession.access_expires_at > now,
                    ACCOUNT_ELIGIBLE,
                )
            )
        return None if user is None else _account(user)

    async def rotate_session(
        self,
        refresh_token_digest: str,
        credentials: SessionCredentials,
        now: datetime,
    ) -> tuple[datetime, datetime] | None:
        """Replace both digests of the live session that the refresh digest names.

        The access expiry is capped at the unchanged session deadline. Only the
        first of several concurrent refreshes with the same digest succeeds.

        Args:
            refresh_token_digest: Digest of the presented refresh credential.
            credentials: New digests and the uncapped access expiry.
            now: Current time.

        Returns:
            The stored access expiry and session deadline, or None when the
            credential is unknown, already rotated, revoked or expired, or the
            account is no longer eligible.
        """

        async with self._transaction() as session:
            user_id = await session.scalar(
                select(AuthSession.user_id).where(AuthSession.refresh_token_digest == refresh_token_digest)
            )
            if user_id is None:
                return None
            await _lock_account(session, user_id)
            eligible_user = select(User.id).where(User.id == AuthSession.user_id, ACCOUNT_ELIGIBLE).exists()
            row = (
                await session.execute(
                    update(AuthSession)
                    .where(
                        AuthSession.refresh_token_digest == refresh_token_digest,
                        *_live_session(now),
                        eligible_user,
                    )
                    .values(
                        access_token_digest=credentials.access_token_digest,
                        refresh_token_digest=credentials.refresh_token_digest,
                        access_expires_at=func.least(credentials.access_expires_at, AuthSession.expires_at),
                    )
                    .returning(AuthSession.access_expires_at, AuthSession.expires_at)
                    .execution_options(synchronize_session=False)
                )
            ).one_or_none()
        return None if row is None else (row[0], row[1])

    async def revoke_session(
        self,
        refresh_token_digest: str | None,
        access_token_digest: str | None,
        now: datetime,
    ) -> LogoutOutcome:
        """Revoke the live session identified by a refresh or access credential.

        A live refresh credential is preferred. If both credentials identify
        different live sessions, neither is changed. The credentials are
        resolved again after the owning accounts are locked, and the session is
        revoked by ID, so a logout still applies after a concurrent refresh
        rotated it.

        Returns:
            REVOKED, NOT_FOUND when no credential identifies a live session, or
            CONFLICT when the credentials identify different sessions.
        """

        async with self._transaction() as session:

            async def resolve() -> tuple[tuple[UUID, UUID] | None, tuple[UUID, UUID] | None]:
                by_refresh = await _live_session_owner(
                    session, AuthSession.refresh_token_digest, refresh_token_digest, now
                )
                by_access = await _live_session_owner(
                    session,
                    AuthSession.access_token_digest,
                    access_token_digest,
                    now,
                    AuthSession.access_expires_at > now,
                )
                return by_refresh, by_access

            before_lock = await resolve()
            owners = {owner[1] for owner in before_lock if owner}
            # Sorted, so two accounts are always locked in the same order.
            for user_id in sorted(owners):
                await _lock_account(session, user_id)
            by_refresh, by_access = await resolve()
            if by_refresh and by_access and by_refresh != by_access:
                return LogoutOutcome.CONFLICT
            # A refresh that won the lock replaced the digests but not the session ID.
            target = by_refresh or by_access or before_lock[0] or before_lock[1]
            if target is None:
                return LogoutOutcome.NOT_FOUND
            session_id = target[0]
            await session.execute(
                update(AuthSession)
                .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now)
                .execution_options(synchronize_session=False)
            )
        return LogoutOutcome.REVOKED


def _live_session(now: datetime) -> tuple:
    return (AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)


async def _lock_account(session: AsyncSession, user_id: UUID) -> None:
    """Lock the account row so session changes for one account run one at a time."""

    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


async def _live_session_owner(
    session: AsyncSession,
    column,
    digest: str | None,
    now: datetime,
    *conditions,
) -> tuple[UUID, UUID] | None:
    """Return (session ID, user ID) of a live session whose digest column matches."""

    if digest is None:
        return None
    row = (
        await session.execute(
            select(AuthSession.id, AuthSession.user_id)
            .join(User, User.id == AuthSession.user_id)
            .where(column == digest, *_live_session(now), *conditions, ACCOUNT_ELIGIBLE)
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])

"""Checks for signup, login, identity, refresh and logout.

Offline tests need no database. PostgreSQL tests need TEST_DATABASE_URL (see
backend/tests/test_database_migrations.py); each class migrates its own
disposable database and drops it afterwards.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest import mock
from uuid import uuid4

from alembic import command
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from backend.api.auth import REFRESH_COOKIE, AuthHttpSettings
from backend.api.auth_schemas import SignupRequest
from backend.app import create_app
from backend.src.services.auth import (
    AuthService,
    ConflictingCredentials,
    InvalidCredentials,
    InvalidSession,
    UsernameTakenError,
    token_digest,
)
from backend.src.storage.app_database import create_session_factory
from backend.src.storage.auth import AuthStore
from backend.tests.test_database_migrations import (
    DATABASE_URL,
    EMBEDDING_DIM,
    DisposableDatabase,
    alembic_config,
)


if sys.platform == "win32":
    # Async psycopg cannot run on the default Windows (Proactor) event loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

FRONTEND_ORIGIN = "https://app.example.test"
PASSWORD = "correct horse battery"
SETTINGS = AuthHttpSettings(allowed_origins=frozenset({FRONTEND_ORIGIN}), secure_cookies=True)
# Cheap parameters keep tests fast; production uses the library defaults.
FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class FakeClock:
    """A controllable current time for session expiry tests."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class SignupSchemaTest(unittest.TestCase):
    def valid(self, **values: Any) -> dict[str, Any]:
        return {"username": "an_nguyen", "display_name": "An", "password": PASSWORD, **values}

    def test_username_is_trimmed_and_lowercased_and_password_kept_exactly(self) -> None:
        request = SignupRequest(**self.valid(username="  An-Nguyen_1 ", password=f" {PASSWORD} "))

        self.assertEqual(request.username, "an-nguyen_1")
        self.assertEqual(request.password, f" {PASSWORD} ")

    def test_invalid_fields_are_rejected(self) -> None:
        for values in (
            {"role": "admin"},
            {"username": "ab"},
            {"username": "a" * 33},
            {"username": "an.nguyen"},
            {"username": "nguyễn"},
            {"display_name": "   "},
            {"display_name": "x" * 81},
            {"display_name": "An\u0000"},
            {"display_name": "An\nNguyen"},
            {"password": "x" * 14},
            {"password": "x" * 129},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    SignupRequest(**self.valid(**values))


class CookieSettingsTest(unittest.TestCase):
    def test_insecure_cookies_require_loopback_origins(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"AUTH_ALLOWED_ORIGINS": "http://127.0.0.1:5173, http://localhost:5173",
             "AUTH_INSECURE_LOOPBACK_COOKIES": "true"},
        ):
            settings = AuthHttpSettings.from_env()
        self.assertFalse(settings.secure_cookies)
        self.assertEqual(settings.allowed_origins, {"http://127.0.0.1:5173", "http://localhost:5173"})

        with mock.patch.dict(
            os.environ,
            {"AUTH_ALLOWED_ORIGINS": "https://factory.example.com",
             "AUTH_INSECURE_LOOPBACK_COOKIES": "true"},
        ):
            with self.assertRaises(ValueError):
                AuthHttpSettings.from_env()

    def test_insecure_cookies_require_configured_origins(self) -> None:
        with mock.patch.dict(
            os.environ, {"AUTH_ALLOWED_ORIGINS": "", "AUTH_INSECURE_LOOPBACK_COOKIES": "true"}
        ):
            with self.assertRaises(ValueError):
                AuthHttpSettings.from_env()

    def test_cookies_are_secure_by_default(self) -> None:
        with mock.patch.dict(os.environ, {"AUTH_ALLOWED_ORIGINS": "", "AUTH_INSECURE_LOOPBACK_COOKIES": ""}):
            settings = AuthHttpSettings.from_env()
        self.assertTrue(settings.secure_cookies)
        self.assertEqual(settings.allowed_origins, frozenset())


class UnavailableAuthTest(unittest.TestCase):
    """Auth failures that need no database."""

    def test_routes_return_503_when_auth_is_not_configured(self) -> None:
        with TestClient(create_app(knowledge_agents={}), base_url="https://testserver") as client:
            login = client.post("/auth/login", json={"username": "an", "password": PASSWORD})
            me = client.get("/auth/me", headers={"Authorization": "Bearer token"})

        self.assertEqual(login.status_code, 503)
        self.assertEqual(me.status_code, 503)

    def test_routes_return_503_when_the_database_is_unreachable(self) -> None:
        # Nothing listens on port 1, so every connection attempt fails.
        engine = create_async_engine(
            "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
            poolclass=NullPool,
            connect_args={"connect_timeout": 1},
        )
        service = AuthService(AuthStore(create_session_factory(engine)), password_hasher=FAST_HASHER)
        app = create_app(knowledge_agents={}, auth_service=service, auth_settings=SETTINGS)
        with TestClient(app, base_url="https://testserver") as client:
            login = client.post("/auth/login", json={"username": "an", "password": PASSWORD})
            me = client.get("/auth/me", headers={"Authorization": "Bearer token"})

        self.assertEqual(login.status_code, 503)
        self.assertEqual(me.status_code, 503)
        self.assertNotIn("nothing", login.text)

    def test_auth_writes_from_other_origins_are_rejected(self) -> None:
        app = create_app(knowledge_agents={}, auth_settings=SETTINGS)
        with TestClient(app, base_url="https://testserver") as client:
            for path in ("/auth/signup", "/auth/login", "/auth/refresh", "/auth/logout"):
                with self.subTest(path=path):
                    response = client.post(path, json={}, headers={"Origin": "https://evil.example"})
                    self.assertEqual(response.status_code, 403)


class PostgresAuthTest(unittest.TestCase):
    """Base class: a migrated disposable database and a service over it."""

    database: DisposableDatabase

    @classmethod
    def setUpClass(cls) -> None:
        cls.database = DisposableDatabase(DATABASE_URL or "")
        cls.database.create()
        try:
            with mock.patch.dict(os.environ, {"EMBEDDING_DIM": str(EMBEDDING_DIM)}):
                command.upgrade(alembic_config(cls.database.url), "head")
            cls.sync_engine = create_engine(cls.database.url)
        except BaseException:
            cls.database.drop()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.sync_engine.dispose()
        cls.database.drop()

    def setUp(self) -> None:
        self.clock = FakeClock()

    def new_service(self) -> tuple[AuthService, AsyncEngine]:
        # NullPool: connections never outlive the event loop that opened them.
        engine = create_async_engine(self.database.url, poolclass=NullPool)
        store = AuthStore(create_session_factory(engine))
        return AuthService(store, clock=self.clock, password_hasher=FAST_HASHER), engine

    def sql(self, statement: str, **params: Any) -> Any:
        with self.sync_engine.begin() as conn:
            return conn.execute(text(statement), params)

    def username(self) -> str:
        return f"user-{uuid4().hex[:8]}"


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class AuthApiTest(PostgresAuthTest):
    def setUp(self) -> None:
        super().setUp()
        service, _engine = self.new_service()
        app = create_app(knowledge_agents={}, auth_service=service, auth_settings=SETTINGS)
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def signup(self, username: str | None = None, **values: Any) -> Any:
        payload = {"username": username or self.username(), "display_name": "An Nguyen",
                   "password": PASSWORD, **values}
        return self.client.post("/auth/signup", json=payload, headers={"Origin": FRONTEND_ORIGIN})

    def login(self, username: str, password: str = PASSWORD) -> Any:
        response = self.client.post(
            "/auth/login", json={"username": username, "password": password},
            headers={"Origin": FRONTEND_ORIGIN},
        )
        # Each test passes cookies explicitly, so devices stay independent.
        self.client.cookies.clear()
        return response

    def device(self, username: str) -> tuple[str, str]:
        """Log in and return the (access, refresh) credentials of a new session."""

        response = self.login(username)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"], response.cookies[REFRESH_COOKIE]

    def me(self, access: str) -> Any:
        return self.client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})

    def refresh(self, refresh: str) -> Any:
        response = self.client.post(
            "/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={refresh}", "Origin": FRONTEND_ORIGIN}
        )
        self.client.cookies.clear()
        return response

    def logout(self, *, access: str | None = None, refresh: str | None = None) -> Any:
        headers = {"Origin": FRONTEND_ORIGIN}
        if access:
            headers["Authorization"] = f"Bearer {access}"
        if refresh:
            headers["Cookie"] = f"{REFRESH_COOKIE}={refresh}"
        response = self.client.post("/auth/logout", headers=headers)
        self.client.cookies.clear()
        return response

    # Signup and login

    def test_signup_creates_an_active_employee_with_a_hashed_password(self) -> None:
        response = self.signup("  New-User ")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(set(body), {"id", "username", "display_name", "role"})
        self.assertEqual((body["username"], body["role"]), ("new-user", "employee"))
        stored_hash, active = self.sql(
            "SELECT password_hash, is_active FROM users WHERE id = :id", id=body["id"]
        ).one()
        self.assertTrue(stored_hash.startswith("$argon2id$"))
        self.assertNotIn(PASSWORD, stored_hash)
        self.assertTrue(active)

    def test_signup_rejects_roles_and_malformed_payloads(self) -> None:
        self.assertEqual(self.signup(role="admin").status_code, 422)
        self.assertEqual(self.signup(display_name="An\u0000").status_code, 422)

        response = self.signup(password="short-secret")

        # Validation errors name the field but never echo submitted values.
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("short-secret", response.text)
        self.assertEqual(response.json()["detail"][0]["loc"], ["body", "password"])

    def test_control_characters_in_login_names_fail_like_bad_credentials(self) -> None:
        response = self.login("an\u0000")

        self.assertEqual(response.status_code, 401)

    def test_used_usernames_are_reserved_even_after_deletion(self) -> None:
        name = self.username()
        self.assertEqual(self.signup(name).status_code, 201)
        self.assertEqual(self.signup(name.upper()).status_code, 409)

        self.sql("UPDATE users SET deleted_at = now() WHERE username = :name", name=name)
        self.assertEqual(self.signup(name).status_code, 409)

    def test_login_issues_tokens_and_a_strict_refresh_cookie(self) -> None:
        name = self.username()
        self.signup(name)

        response = self.login(f" {name.upper()} ")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"access_token", "token_type", "expires_in"})
        self.assertEqual((body["token_type"], body["expires_in"]), ("bearer", 900))
        self.assertEqual(response.headers["cache-control"], "no-store")
        cookie = response.headers["set-cookie"].lower()
        for attribute in ("httponly", "secure", "samesite=strict", "path=/auth", "max-age=259200"):
            self.assertIn(attribute, cookie)

        refresh = response.cookies[REFRESH_COOKIE]
        access_digest, refresh_digest = self.sql(
            "SELECT access_token_digest, refresh_token_digest FROM auth_sessions s "
            "JOIN users u ON u.id = s.user_id WHERE u.username = :name", name=name,
        ).one()
        self.assertEqual(access_digest, token_digest(body["access_token"]))
        self.assertEqual(refresh_digest, token_digest(refresh))

    def test_login_failures_are_indistinguishable(self) -> None:
        inactive, deleted, valid = self.username(), self.username(), self.username()
        for name in (inactive, deleted, valid):
            self.signup(name)
        self.sql("UPDATE users SET is_active = false WHERE username = :name", name=inactive)
        self.sql("UPDATE users SET deleted_at = now() WHERE username = :name", name=deleted)

        failures = [
            self.login("nobody-here"),
            self.login(valid, "wrong password value"),
            self.login(inactive),
            self.login(deleted),
            self.login("   "),
        ]

        for response in failures:
            self.assertEqual((response.status_code, response.json()), (401, failures[0].json()))
            self.assertNotIn("set-cookie", response.headers)

    def test_unsupported_stored_hash_formats_fail_login(self) -> None:
        name = self.username()
        self.signup(name)
        self.sql("UPDATE users SET password_hash = '$2b$12$legacy' WHERE username = :name", name=name)

        self.assertEqual(self.login(name).status_code, 401)

    # Identity, refresh and logout

    def test_me_returns_the_current_role(self) -> None:
        name = self.username()
        user_id = self.signup(name).json()["id"]
        access, _refresh = self.device(name)

        self.sql("UPDATE users SET role = 'expert' WHERE id = :id", id=user_id)
        response = self.me(access)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": user_id, "username": name,
                                           "display_name": "An Nguyen", "role": "expert"})
        self.assertEqual(self.me("not-a-token").status_code, 401)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_logout_revokes_the_same_access_token_and_keeps_other_devices(self) -> None:
        name = self.username()
        self.signup(name)
        first_access, first_refresh = self.device(name)
        second_access, second_refresh = self.device(name)

        response = self.logout(access=first_access, refresh=first_refresh)

        self.assertEqual(response.status_code, 204)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn(f"{REFRESH_COOKIE}=", cookie)
        self.assertIn("max-age=0", cookie)
        self.assertIn("path=/auth", cookie)
        self.assertEqual(self.me(first_access).status_code, 401)
        self.assertEqual(self.refresh(first_refresh).status_code, 401)
        self.assertEqual(self.logout(access=first_access, refresh=first_refresh).status_code, 204)
        self.assertEqual(self.logout().status_code, 204)
        self.assertEqual(self.me(second_access).status_code, 200)
        self.assertEqual(self.refresh(second_refresh).status_code, 200)

    def test_refresh_cookie_logs_out_after_access_expiry(self) -> None:
        name = self.username()
        self.signup(name)
        access, refresh = self.device(name)
        self.clock.advance(minutes=20)

        self.assertEqual(self.logout(access=access, refresh=refresh).status_code, 204)

        self.assertEqual(self.refresh(refresh).status_code, 401)

    def test_logout_with_credentials_of_two_sessions_changes_nothing(self) -> None:
        name = self.username()
        self.signup(name)
        first_access, _ = self.device(name)
        second_access, second_refresh = self.device(name)

        response = self.logout(access=first_access, refresh=second_refresh)

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.me(first_access).status_code, 200)
        self.assertEqual(self.me(second_access).status_code, 200)

    def test_access_expires_and_refresh_never_extends_the_session_deadline(self) -> None:
        name = self.username()
        self.signup(name)
        access, refresh = self.device(name)

        self.clock.advance(minutes=15)
        self.assertEqual(self.me(access).status_code, 401)

        response = self.refresh(refresh)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["expires_in"], 900)
        self.assertEqual(response.headers["cache-control"], "no-store")
        access, refresh = response.json()["access_token"], response.cookies[REFRESH_COOKIE]
        self.assertEqual(self.me(access).status_code, 200)

        # 72 h after login minus 5 min: access is capped at the deadline.
        self.clock.advance(hours=71, minutes=40)
        response = self.refresh(refresh)
        self.assertEqual(response.json()["expires_in"], 300)
        self.assertIn("max-age=300", response.headers["set-cookie"].lower())
        access, refresh = response.json()["access_token"], response.cookies[REFRESH_COOKIE]

        self.clock.advance(minutes=5)
        self.assertEqual(self.me(access).status_code, 401)
        self.assertEqual(self.refresh(refresh).status_code, 401)

    def test_reused_refresh_token_fails_without_touching_the_cookie(self) -> None:
        name = self.username()
        self.signup(name)
        _access, refresh = self.device(name)
        winner = self.refresh(refresh)
        self.assertEqual(winner.status_code, 200)

        reused = self.refresh(refresh)

        self.assertEqual(reused.status_code, 401)
        self.assertNotIn("set-cookie", reused.headers)
        self.assertEqual(self.refresh(winner.cookies[REFRESH_COOKIE]).status_code, 200)

    def test_disabled_and_deleted_accounts_lose_their_sessions(self) -> None:
        for change in ("is_active = false", "deleted_at = now()"):
            with self.subTest(change=change):
                name = self.username()
                self.signup(name)
                access, refresh = self.device(name)

                self.sql(f"UPDATE users SET {change} WHERE username = :name", name=name)

                self.assertEqual(self.me(access).status_code, 401)
                self.assertEqual(self.refresh(refresh).status_code, 401)


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL is not set")
class AuthConcurrencyTest(PostgresAuthTest):
    """Races coordinated by holding the account row lock in another connection."""

    def run_async(self, awaitable: Awaitable[Any]) -> Any:
        return asyncio.run(awaitable)

    async def with_account_locked(self, user_id: str, waiters: int, *calls: Awaitable[Any],
                                  while_locked: str | None = None) -> list[Any]:
        """Start calls while the account row is locked, release it once all wait.

        Args:
            user_id: Account whose row is locked.
            waiters: Number of calls expected to block on the lock.
            calls: Service calls to race.
            while_locked: Optional SQL run in the locking transaction before release.

        Returns:
            Each call's result or raised exception, in order.
        """

        holder = self.sync_engine.connect()
        try:
            transaction = holder.begin()
            holder.execute(text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": user_id})
            tasks = [asyncio.ensure_future(call) for call in calls]
            for _ in range(500):
                # Statistics views are cached per transaction; read them afresh.
                holder.execute(text("SELECT pg_stat_clear_snapshot()"))
                blocked = holder.scalar(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock'"
                ))
                if blocked == waiters:
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail(f"Expected {waiters} calls to wait for the account lock")
            if while_locked:
                holder.execute(text(while_locked), {"id": user_id})
            transaction.commit()
        finally:
            holder.close()
        return await asyncio.gather(*tasks, return_exceptions=True)

    def test_concurrent_duplicate_signup_creates_one_account(self) -> None:
        name = self.username()

        async def race() -> list[Any]:
            service, engine = self.new_service()
            try:
                return await asyncio.gather(
                    service.signup(name, "First", PASSWORD),
                    service.signup(name, "Second", PASSWORD),
                    return_exceptions=True,
                )
            finally:
                await engine.dispose()

        results = self.run_async(race())

        self.assertEqual(sum(isinstance(result, UsernameTakenError) for result in results), 1)
        self.assertEqual(self.sql("SELECT count(*) FROM users WHERE username = :n", n=name).scalar(), 1)

    def test_simultaneous_refresh_has_one_winner(self) -> None:
        async def race() -> list[Any]:
            service, engine = self.new_service()
            try:
                account = await service.signup(self.username(), "An", PASSWORD)
                tokens = await service.login(account.username, PASSWORD)
                results = await self.with_account_locked(
                    str(account.id), 2,
                    service.refresh(tokens.refresh_token), service.refresh(tokens.refresh_token),
                )
                winners = [result for result in results if not isinstance(result, Exception)]
                self.assertEqual(len(winners), 1)
                self.assertEqual(sum(isinstance(result, InvalidSession) for result in results), 1)
                await service.authenticate_access(winners[0].access_token)
                with self.assertRaises(InvalidSession):
                    await service.authenticate_access(tokens.access_token)
                return results
            finally:
                await engine.dispose()

        self.run_async(race())

    def test_logout_racing_refresh_revokes_the_session(self) -> None:
        async def race() -> None:
            service, engine = self.new_service()
            try:
                account = await service.signup(self.username(), "An", PASSWORD)
                tokens = await service.login(account.username, PASSWORD)
                results = await self.with_account_locked(
                    str(account.id), 2,
                    service.refresh(tokens.refresh_token),
                    service.logout(tokens.refresh_token, None),
                )
                self.assertIsNone(results[1])
                live = self.sql(
                    "SELECT count(*) FROM auth_sessions WHERE user_id = :id AND revoked_at IS NULL",
                    id=account.id,
                ).scalar()
                self.assertEqual(live, 0)
                if not isinstance(results[0], Exception):
                    with self.assertRaises(InvalidSession):
                        await service.refresh(results[0].refresh_token)
            finally:
                await engine.dispose()

        self.run_async(race())

    def test_login_racing_account_disablement_creates_no_session(self) -> None:
        async def race() -> None:
            service, engine = self.new_service()
            try:
                account = await service.signup(self.username(), "An", PASSWORD)
                results = await self.with_account_locked(
                    str(account.id), 1,
                    service.login(account.username, PASSWORD),
                    while_locked="UPDATE users SET is_active = false WHERE id = :id",
                )
                self.assertIsInstance(results[0], InvalidCredentials)
                sessions = self.sql(
                    "SELECT count(*) FROM auth_sessions WHERE user_id = :id", id=account.id
                ).scalar()
                self.assertEqual(sessions, 0)
            finally:
                await engine.dispose()

        self.run_async(race())

    def test_conflicting_logout_is_reported(self) -> None:
        async def run() -> None:
            service, engine = self.new_service()
            try:
                account = await service.signup(self.username(), "An", PASSWORD)
                first = await service.login(account.username, PASSWORD)
                second = await service.login(account.username, PASSWORD)
                with self.assertRaises(ConflictingCredentials):
                    await service.logout(first.refresh_token, second.access_token)
            finally:
                await engine.dispose()

        self.run_async(run())


if __name__ == "__main__":
    unittest.main()

"""Shared sign-in accounts for demos: one employee, one admin and one expert.

The usernames and passwords are kept in a JSON file under `data/`, which Git
ignores. The file is written on the first run with random passwords and read
on later runs, so the accounts in the database always match the file. The
expert is the mock expert of the scripted incident demo, so tickets sent to it
can be reviewed by signing in with the file's password.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.services.demo_scenario import DemoExpert
from backend.src.storage.app_database import app_transaction
from backend.src.storage.orm.users import User, UserRole, normalize_username


ACCOUNTS_PATH = Path("data/demo/accounts.json")
_NOTE = (
    "Demo accounts only. Created by backend/scripts/demo_accounts.py; "
    "run it again after editing this file. Do not commit or reuse these passwords."
)


class DemoAccount(BaseModel):
    """One demo account as stored in the credentials file."""

    model_config = ConfigDict(extra="forbid")

    purpose: str
    role: UserRole
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=15, max_length=128)


class DemoAccounts(BaseModel):
    """Content of the credentials file."""

    model_config = ConfigDict(extra="forbid")

    note: str = _NOTE
    accounts: list[DemoAccount] = Field(min_length=1)


def _new_accounts(expert: DemoExpert) -> DemoAccounts:
    """Return the three default accounts with new random passwords."""

    def account(purpose: str, role: UserRole, username: str, display_name: str) -> DemoAccount:
        return DemoAccount(
            purpose=purpose, role=role, username=username, display_name=display_name,
            password=secrets.token_urlsafe(15),
        )

    return DemoAccounts(
        accounts=[
            account(
                "Shared employee account: chat, tickets, file library",
                UserRole.EMPLOYEE, "demo.user", "Nhân viên vận hành (demo)",
            ),
            account(
                "Admin: creates and manages knowledge bases",
                UserRole.ADMIN, "demo.admin", "Quản trị viên (demo)",
            ),
            account(
                "Expert: reviews knowledge and tickets sent for review",
                UserRole.EXPERT, expert.username, expert.display_name,
            ),
        ]
    )


def load_or_create_accounts(path: Path, expert: DemoExpert) -> DemoAccounts:
    """Read the credentials file, or write it with new random passwords if it is missing.

    Args:
        path: Location of the credentials file.
        expert: Username and display name used for the expert account in a new file.

    Returns:
        The accounts in the file. An existing file is never rewritten.

    Raises:
        OSError: The file cannot be read or written.
        pydantic.ValidationError: An existing file does not match `DemoAccounts`.
    """

    if path.exists():
        return DemoAccounts.model_validate_json(path.read_text(encoding="utf-8"))
    accounts = _new_accounts(expert)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only from the start: the file holds passwords.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(accounts.model_dump_json(indent=2) + "\n")
    return accounts


async def ensure_demo_accounts(
    sessions: async_sessionmaker[AsyncSession],
    accounts: DemoAccounts,
    *,
    password_hasher: PasswordHasher | None = None,
) -> list[str]:
    """Make each account in the database match the credentials file.

    A missing username is created. An existing one gets the file's password,
    role and display name, and is made active again; its sessions are kept.

    Args:
        sessions: Application database sessions.
        accounts: Accounts from the credentials file.
        password_hasher: Argon2id hasher; tests may pass cheaper parameters.

    Returns:
        One line per account: `created <username>` or `updated <username>`.

    Raises:
        AppDatabaseUnavailable: The application database could not be reached.
    """

    hasher = password_hasher or PasswordHasher()
    done = []
    async with app_transaction(sessions) as session:
        for account in accounts.accounts:
            username = normalize_username(account.username)
            values = {
                "display_name": account.display_name,
                "password_hash": await asyncio.to_thread(hasher.hash, account.password),
                "role": account.role.value,
            }
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                session.add(User(username=username, **values))
                done.append(f"created {username}")
                continue
            for name, value in values.items():
                setattr(user, name, value)
            user.is_active = True
            user.deleted_at = None
            done.append(f"updated {username}")
    return done

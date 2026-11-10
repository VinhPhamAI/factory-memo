"""Create the shared demo accounts and keep their credentials in `data/demo/accounts.json`.

Run `make demo-accounts` (Docker database) or
`uv run python -m backend.scripts.demo_accounts` (host). Needs
`APP_DATABASE_URL`. Passwords are written to the file only, never printed.
See docs/development/demo-incident-flow.md.
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from backend.src.services.demo_accounts import (
    ACCOUNTS_PATH,
    ensure_demo_accounts,
    load_or_create_accounts,
)
from backend.src.services.demo_scenario import read_scenario
from backend.src.storage.app_database import (
    app_database_url_from_env,
    create_app_engine,
    create_session_factory,
)


async def create_accounts() -> list[str]:
    """Create or update the demo accounts from the credentials file; return what was done.

    Raises:
        SystemExit: `APP_DATABASE_URL` is not set.
        AppDatabaseUnavailable: The application database could not be reached.
    """

    url = app_database_url_from_env()
    if not url:
        raise SystemExit("APP_DATABASE_URL is not set")
    accounts = load_or_create_accounts(ACCOUNTS_PATH, read_scenario().expert)
    engine = create_app_engine(url)
    try:
        return await ensure_demo_accounts(create_session_factory(engine), accounts)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    load_dotenv()
    for line in asyncio.run(create_accounts()):
        print(line)
    print(f"Credentials: {ACCOUNTS_PATH}")

"""Create the shared demo accounts and keep their credentials in `accounts.json` here.

Run `make demo-accounts` (Docker database) or
`uv run python -m backend.src.services.demo.create_accounts` (host). Needs
`APP_DATABASE_URL`. Passwords are written to the file only, never printed.
See docs/development/demo-incident-flow.md.
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from backend.src.services.demo.accounts import (
    ACCOUNTS_PATH,
    ensure_demo_accounts,
    load_or_create_accounts,
)
from backend.src.services.demo.scenario import read_scenario
from backend.src.services.demo.tickets import read_ticket_seed, seed_demo_tickets
from backend.src.storage.app_database import (
    app_database_url_from_env,
    create_app_engine,
    create_session_factory,
)


async def create_accounts() -> list[str]:
    """Create or update the demo accounts from the credentials file; return what was done.

    Also saves the mock tickets of the board when none exist yet.

    Raises:
        SystemExit: `APP_DATABASE_URL` is not set.
        AppDatabaseUnavailable: The application database could not be reached.
    """

    url = app_database_url_from_env()
    if not url:
        raise SystemExit("APP_DATABASE_URL is not set")
    scenario = read_scenario()
    accounts = load_or_create_accounts(ACCOUNTS_PATH, scenario.expert)
    engine = create_app_engine(url)
    try:
        sessions = create_session_factory(engine)
        done = await ensure_demo_accounts(sessions, accounts)
        seeded = await seed_demo_tickets(sessions, read_ticket_seed(), scenario.expert)
        if seeded:
            done.append(f"saved {seeded} mock ticket(s)")
        return done
    finally:
        await engine.dispose()


if __name__ == "__main__":
    load_dotenv()
    for line in asyncio.run(create_accounts()):
        print(line)
    print("Credentials: backend/src/services/demo/accounts.json")

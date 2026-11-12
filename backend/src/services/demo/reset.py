"""Hide the tickets and chats left by earlier runs of the demo and restore the mock board.

Run `make demo-reset` (Docker API) or `uv run python -m backend.src.services.demo.reset`
(host). Needs `APP_DATABASE_URL`; works whether or not `DEMO_MODE` is set and
needs no API restart. Mock board tickets are replaced by a fresh set when the
demo accounts exist. See docs/development/demo-incident-flow.md.
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from backend.src.services.demo.scenario import read_scenario, reset_demo_data
from backend.src.services.demo.tickets import DemoAccountMissing, read_ticket_seed, seed_demo_tickets
from backend.src.storage.app_database import (
    app_database_url_from_env,
    create_app_engine,
    create_session_factory,
)


async def reset() -> tuple[int, int, int | None]:
    """Reset the demo data in the application database.

    Hides the scripted tickets and chats, then hides the mock board tickets
    and saves a fresh set.

    Returns:
        `(tickets hidden, chats hidden, mock tickets saved)`; the last is None
        when the demo accounts do not exist yet.

    Raises:
        SystemExit: `APP_DATABASE_URL` is not set.
        AppDatabaseUnavailable: The application database could not be reached.
    """

    url = app_database_url_from_env()
    if not url:
        raise SystemExit("APP_DATABASE_URL is not set")
    engine = create_app_engine(url)
    try:
        sessions = create_session_factory(engine)
        scenario = read_scenario()
        tickets, chats = await reset_demo_data(sessions, scenario)
        try:
            seeded = await seed_demo_tickets(sessions, read_ticket_seed(), scenario.expert, replace=True)
        except DemoAccountMissing:
            seeded = None
        return tickets, chats, seeded
    finally:
        await engine.dispose()


if __name__ == "__main__":
    load_dotenv()
    tickets, chats, seeded = asyncio.run(reset())
    print(f"Demo reset: {tickets} ticket(s) and {chats} chat(s) hidden. Start a new chat to run it again.")
    if seeded is None:
        print("Mock board not restored: run `make demo-accounts` first.")
    else:
        print(f"Mock board restored with {seeded} ticket(s).")

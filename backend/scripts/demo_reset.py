"""Hide the tickets and chats left by earlier runs of the scripted incident demo.

Run `make demo-reset` (Docker API) or `uv run python -m backend.scripts.demo_reset`
(host). Needs `APP_DATABASE_URL`; works whether or not `DEMO_MODE` is set and
needs no API restart. See docs/development/demo-incident-flow.md.
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from backend.src.services.demo_scenario import read_scenario, reset_demo_data
from backend.src.storage.app_database import (
    app_database_url_from_env,
    create_app_engine,
    create_session_factory,
)


async def reset() -> tuple[int, int]:
    """Reset the demo data in the application database; return `(tickets, chats)` hidden.

    Raises:
        SystemExit: `APP_DATABASE_URL` is not set.
        AppDatabaseUnavailable: The application database could not be reached.
    """

    url = app_database_url_from_env()
    if not url:
        raise SystemExit("APP_DATABASE_URL is not set")
    engine = create_app_engine(url)
    try:
        return await reset_demo_data(create_session_factory(engine), read_scenario())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    load_dotenv()
    tickets, chats = asyncio.run(reset())
    print(f"Demo reset: {tickets} ticket(s) and {chats} chat(s) hidden. Start a new chat to run it again.")

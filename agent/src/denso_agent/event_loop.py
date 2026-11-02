"""Select an event loop compatible with psycopg on Windows."""

import asyncio
import sys


def new_event_loop() -> asyncio.AbstractEventLoop:
    """Create a psycopg-compatible loop in native and reload child processes.

    Returns:
        A Windows selector loop or the platform's ordinary event loop.
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()

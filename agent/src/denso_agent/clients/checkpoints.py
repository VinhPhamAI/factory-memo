"""Own runtime PostgreSQL connections without applying migrations."""

import asyncio
from contextlib import asynccontextmanager

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


@asynccontextmanager
async def checkpoint_store(database_url: str):
    """Yield a saver backed by a restricted, explicitly closed connection pool.

    Args:
        database_url: Native PostgreSQL DSN for the runtime role.

    Yields:
        A saver whose connections use autocommit and dictionary rows.
    """
    async with AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=4,
        open=False,
        timeout=5,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "connect_timeout": 5,
            "row_factory": dict_row,
        },
    ) as pool:
        await pool.wait(timeout=5)
        yield AsyncPostgresSaver(pool)


async def check_readiness(saver: AsyncPostgresSaver) -> bool:
    """Check checkpoint tables and schema version within a short deadline.

    Args:
        saver: Runtime saver with an already opened pool.

    Returns:
        Whether the required schema exists at the locked migration version.
    """
    try:
        async with asyncio.timeout(3):
            async with saver.conn.connection() as connection:
                row = await (
                    await connection.execute(
                        "SELECT max(v) AS version FROM checkpoint_migrations"
                    )
                ).fetchone()
                if row["version"] != len(saver.MIGRATIONS) - 1:
                    return False
                await connection.execute("SELECT 1 FROM checkpoints LIMIT 0")
                await connection.execute("SELECT 1 FROM checkpoint_blobs LIMIT 0")
                await connection.execute("SELECT 1 FROM checkpoint_writes LIMIT 0")
                return True
    except (psycopg.Error, TimeoutError):
        return False

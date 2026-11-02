"""Verify real checkpoint setup, data preservation and runtime permissions."""

import psycopg
import pytest

from denso_agent.clients.checkpoints import checkpoint_store, check_readiness
from denso_agent.config import SetupSettings
from denso_agent.setup_database import setup_database


async def test_setup_rerun_preserves_data_and_runtime_is_restricted(database):
    """Run migrations twice and prove runtime reads without schema privileges."""
    settings = SetupSettings(**database)
    await setup_database(settings)
    async with await psycopg.AsyncConnection.connect(
        database["setup_database_url"], autocommit=True
    ) as connection:
        await connection.execute("CREATE TABLE future_checkpoint_table (value int)")
    async with checkpoint_store(database["database_url"]) as saver:
        assert await check_readiness(saver)
        async with saver.conn.connection() as connection:
            await connection.execute(
                "INSERT INTO checkpoint_blobs(thread_id, checkpoint_ns, channel, version, type, blob) "
                "VALUES ('setup-preserve', '', 'fixture', '1', 'empty', NULL)"
            )
    await setup_database(settings)
    async with await psycopg.AsyncConnection.connect(
        database["database_url"], autocommit=True
    ) as connection:
        row = await (
            await connection.execute(
                "SELECT thread_id FROM checkpoint_blobs WHERE thread_id='setup-preserve'"
            )
        ).fetchone()
        assert row == ("setup-preserve",)
        await connection.execute("INSERT INTO future_checkpoint_table VALUES (7)")
        assert await (
            await connection.execute("SELECT value FROM future_checkpoint_table")
        ).fetchone() == (7,)
        for forbidden in (
            "INSERT INTO checkpoint_migrations(v) VALUES (999999)",
            "CREATE TABLE forbidden_table (id int)",
            "CREATE SCHEMA forbidden_schema",
            "CREATE ROLE forbidden_role",
            "CREATE DATABASE forbidden_database",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await connection.execute(forbidden)


async def test_readiness_rejects_unmigrated_database(database):
    """Detect missing saver tables without creating them during readiness."""
    await setup_database(SetupSettings(**database))
    async with await psycopg.AsyncConnection.connect(
        database["setup_database_url"], autocommit=True
    ) as connection:
        await connection.execute("DROP TABLE checkpoint_writes")
    async with checkpoint_store(database["database_url"]) as saver:
        assert not await check_readiness(saver)


async def test_setup_rejects_runtime_role_membership(database):
    """Reject inherited permissions instead of silently claiming runtime isolation."""
    await setup_database(SetupSettings(**database))
    async with await psycopg.AsyncConnection.connect(
        database["bootstrap_database_url"], autocommit=True
    ) as connection:
        await connection.execute("GRANT pg_read_all_data TO agent_runtime")
    with pytest.raises(ValueError, match="membership"):
        await setup_database(SetupSettings(**database))


async def test_setup_refuses_existing_database_owned_by_another_role(database):
    """Leave an unrelated pre-existing database and bootstrap role unchanged."""
    async with await psycopg.AsyncConnection.connect(
        database["bootstrap_database_url"], autocommit=True
    ) as connection:
        await connection.execute("CREATE DATABASE agent_memory")
    with pytest.raises(ValueError, match="another owner"):
        await setup_database(SetupSettings(**database))

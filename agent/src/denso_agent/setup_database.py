"""Provision the isolated checkpoint database and restricted runtime login."""

import re

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from denso_agent.config import SetupSettings

DATABASE_NAME = "agent_memory"
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


def _credential(database_url: str) -> dict[str, str]:
    """Validate an agent role URL before any administrative connection opens.

    Args:
        database_url: Native DSN containing an agent role and password.

    Returns:
        Parsed connection fields with a valid fixed database and role.

    Raises:
        ValueError: The URL targets another database or has unsafe credentials.
    """
    fields = conninfo_to_dict(database_url)
    if (
        fields.get("dbname") != DATABASE_NAME
        or not IDENTIFIER.fullmatch(fields.get("user", ""))
        or not fields.get("password")
        or fields["user"] in {"postgres", "public"}
    ):
        raise ValueError("Agent credentials require agent_memory and a dedicated login")
    return fields


async def setup_database(settings: SetupSettings) -> None:
    """Create agent roles, migrate checkpoints and grant restricted runtime access.

    Args:
        settings: Bootstrap, setup-owner and runtime connection URLs.

    Raises:
        ValueError: Roles, database names or connection targets are inconsistent.
        psycopg.Error: Provisioning or a checkpoint migration fails.
    """
    owner = _credential(settings.setup_database_url)
    runtime = _credential(settings.database_url)
    bootstrap = conninfo_to_dict(settings.bootstrap_database_url)
    targets = ("host", "hostaddr", "port")
    if (
        owner["user"] != "agent_owner"
        or runtime["user"] != "agent_runtime"
        or bootstrap.get("user") in {owner["user"], runtime["user"]}
        or any(owner.get(key) != runtime.get(key) for key in targets)
        or any(owner.get(key) != bootstrap.get(key) for key in targets)
    ):
        raise ValueError("Setup URLs must use separate roles on the same server")
    async with await psycopg.AsyncConnection.connect(
        settings.bootstrap_database_url, autocommit=True, connect_timeout=10
    ) as connection:
        database_record = await (
            await connection.execute(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s",
                (DATABASE_NAME,),
            )
        ).fetchone()
        if database_record and database_record[0] != owner["user"]:
            raise ValueError("Existing agent_memory belongs to another owner")
        memberships = await (
            await connection.execute(
                "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
                "WHERE r.rolname IN (%s, %s) LIMIT 1",
                (owner["user"], runtime["user"]),
            )
        ).fetchone()
        if memberships:
            raise ValueError("Agent roles must not inherit membership in other roles")
        for credential in (owner, runtime):
            role = sql.Identifier(credential["user"])
            exists = await (
                await connection.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=%s", (credential["user"],)
                )
            ).fetchone()
            if not exists:
                await connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(role))
            await connection.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(role, sql.Literal(credential["password"]))
            )
        if not database_record:
            await connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(DATABASE_NAME), sql.Identifier(owner["user"])
                )
            )
    async with await psycopg.AsyncConnection.connect(
        settings.setup_database_url,
        autocommit=True,
        connect_timeout=10,
        row_factory=dict_row,
        prepare_threshold=0,
    ) as connection:
        await connection.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(DATABASE_NAME)
            )
        )
        await connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        await AsyncPostgresSaver(connection).setup()
        runtime_role = sql.Identifier(runtime["user"])
        await connection.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(
                sql.Identifier(DATABASE_NAME), runtime_role
            )
        )
        await connection.execute(
            sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(runtime_role)
        )
        await connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(DATABASE_NAME), runtime_role
            )
        )
        await connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(runtime_role)
        )
        await connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(runtime_role)
        )
        await connection.execute(
            sql.SQL(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {}"
            ).format(runtime_role)
        )
        await connection.execute(
            sql.SQL(
                "REVOKE INSERT, UPDATE, DELETE ON checkpoint_migrations FROM {}"
            ).format(runtime_role)
        )
        await connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, "
                "DELETE ON TABLES TO {}"
            ).format(runtime_role)
        )
        await connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE "
                "ON SEQUENCES TO {}"
            ).format(runtime_role)
        )

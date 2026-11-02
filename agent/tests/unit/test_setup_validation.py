"""Reject administrative targets before opening database connections."""

from unittest.mock import AsyncMock

import pytest

from denso_agent.config import SetupSettings
from denso_agent.setup_database import setup_database


@pytest.mark.parametrize(
    "owner,runtime",
    [
        (
            "postgresql://owner:pw@localhost/backend",
            "postgresql://runtime:pw@localhost/agent_memory",
        ),
        (
            "postgresql://agent_owner:pw@localhost/agent_memory",
            "postgresql://agent_owner:pw@localhost/agent_memory",
        ),
        (
            "postgresql://agent_owner:pw@localhost/agent_memory",
            "postgresql://agent_runtime:pw@elsewhere/agent_memory",
        ),
        (
            "postgresql://agent_owner:pw@localhost/agent_memory",
            "postgresql://postgres:pw@localhost/agent_memory",
        ),
        (
            "postgresql://agent_owner:pw@localhost/agent_memory",
            "postgresql://agent_runtime@localhost/agent_memory",
        ),
        (
            "postgresql://voice_agent:pw@localhost/agent_memory",
            "postgresql://agent_runtime:pw@localhost/agent_memory",
        ),
    ],
)
async def test_invalid_setup_targets_never_connect(monkeypatch, owner, runtime):
    """Reject wrong database, shared roles, remote targets and missing passwords."""
    connect = AsyncMock()
    monkeypatch.setattr(
        "denso_agent.setup_database.psycopg.AsyncConnection.connect", connect
    )
    settings = SetupSettings(
        bootstrap_database_url="postgresql://postgres:pw@localhost/postgres",
        setup_database_url=owner,
        database_url=runtime,
    )
    with pytest.raises(ValueError):
        await setup_database(settings)
    connect.assert_not_called()

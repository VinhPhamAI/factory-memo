"""Verify resource ownership independently of a database or provider."""

from contextlib import asynccontextmanager
from unittest.mock import Mock

import pytest

from denso_agent.app import create_app
from denso_agent.config import RuntimeSettings


@pytest.mark.parametrize("database_fails", [False, True])
async def test_lifespan_closes_opened_resources(monkeypatch, database_fails):
    """Shutdown and failed database startup close every already-opened client."""
    events = []
    settings = RuntimeSettings(
        api_token="secret",
        api_key="key",
        model="test",
        base_url="http://example.test",
        database_url="postgresql://test:pass@localhost/agent_memory",
    )

    @asynccontextmanager
    async def provider(_settings):
        """Record provider ownership without opening a network connection."""
        events.append("provider-open")
        try:
            yield object()
        finally:
            events.append("provider-close")

    @asynccontextmanager
    async def checkpoints(_url):
        """Simulate ordinary database ownership or failure during startup."""
        if database_fails:
            raise RuntimeError("database unavailable")
        events.append("database-open")
        try:
            yield object()
        finally:
            events.append("database-close")

    monkeypatch.setattr("denso_agent.clients.modelark.ModelArk", provider)
    monkeypatch.setattr("denso_agent.clients.checkpoints.checkpoint_store", checkpoints)
    monkeypatch.setattr(
        "denso_agent.runtime.graph.build_graph", Mock(return_value=object())
    )
    app = create_app(settings=settings)
    assert events == []
    if database_fails:
        with pytest.raises(RuntimeError, match="database unavailable"):
            async with app.router.lifespan_context(app):
                pytest.fail("Failed startup cannot begin serving.")
        assert events == ["provider-open", "provider-close"]
    else:
        async with app.router.lifespan_context(app):
            assert app.state.runtime is not None
        assert app.state.runtime is None
        assert events == [
            "provider-open",
            "database-open",
            "database-close",
            "provider-close",
        ]

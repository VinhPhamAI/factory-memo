"""Specify independent command and configuration behavior."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from denso_agent.cli import main
from denso_agent.config import RuntimeSettings, SetupSettings


def test_help_needs_no_credentials(capsys):
    """Help exits before constructing settings or opening resources."""
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    assert "setup-db" in capsys.readouterr().out


def test_serve_uses_source_reload(monkeypatch):
    """Reload watches the package source and starts one worker."""
    calls = []
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        "uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    main(["serve", "--reload"])
    args, options = calls[0]
    assert args == ("denso_agent.app:create_app",)
    assert options["host"] == "127.0.0.1"
    assert options["port"] == 8010
    assert options["workers"] == 1
    assert Path(options["reload_dirs"][0]).name == "src"


def test_windows_reload_uses_bounded_supervisor(monkeypatch):
    """Native Windows reload binds one socket and owns one supervised worker."""
    monkeypatch.setattr("sys.platform", "win32")
    config = Mock()
    server = Mock()
    supervisor = Mock()
    config_factory = Mock(return_value=config)
    supervisor_factory = Mock(return_value=supervisor)
    monkeypatch.setattr("uvicorn.Config", config_factory)
    monkeypatch.setattr("uvicorn.Server", Mock(return_value=server))
    monkeypatch.setattr("denso_agent.windows_reload.WindowsReload", supervisor_factory)
    monkeypatch.setattr(
        "uvicorn.run",
        Mock(side_effect=AssertionError("Default Windows reload must not run.")),
    )
    main(["serve", "--reload"])
    assert config_factory.call_args.args == ("denso_agent.app:create_app",)
    assert config_factory.call_args.kwargs["factory"] is True
    assert config_factory.call_args.kwargs["workers"] == 1
    assert (
        config_factory.call_args.kwargs["loop"]
        == "denso_agent.event_loop:new_event_loop"
    )
    supervisor_factory.assert_called_once_with(
        config, target=server.run, sockets=[config.bind_socket.return_value]
    )
    supervisor.run.assert_called_once()


def test_setup_settings_require_no_provider():
    """Provisioning accepts only its database credentials."""
    settings = SetupSettings(
        AGENT_BOOTSTRAP_DATABASE_URL="postgresql://admin:pass@localhost/postgres",
        AGENT_SETUP_DATABASE_URL="postgresql://agent_owner:pass@localhost/agent_memory",
        AGENT_DATABASE_URL="postgresql://agent_runtime:pass@localhost/agent_memory",
    )
    assert "agent_memory" in settings.database_url


def test_runtime_defaults():
    """Runtime limits match the reviewed demo defaults."""
    settings = RuntimeSettings(
        AGENT_API_TOKEN="test-secret",
        MODELARK_API_KEY="provider-key",
        MODELARK_BASE_URL="https://example.test/api/v3",
        MODELARK_MODEL="test-model",
        AGENT_DATABASE_URL="postgresql://agent_runtime:pass@localhost/agent_memory",
    )
    assert settings.request_timeout == 120
    assert settings.max_concurrent_requests == 4
    assert settings.context_max_messages == 40


@pytest.mark.parametrize("argument", ["unknown", "serve --port 0"])
def test_bad_command_is_rejected(argument):
    """Invalid commands and unusable listener ports fail argument parsing."""
    with pytest.raises(SystemExit) as result:
        main(argument.split())
    assert result.value.code == 2

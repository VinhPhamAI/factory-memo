"""Exercise bounded worker replacement without Windows console signals."""

from unittest.mock import Mock, patch

import pytest

from denso_agent.windows_reload import WindowsReload


def test_reload_stops_old_worker_before_starting_replacement():
    """A worker that ignores termination is killed before a new worker starts."""
    supervisor = object.__new__(WindowsReload)
    old = Mock()
    old.is_alive.side_effect = [True, True, False]
    supervisor.process = old
    supervisor.config = Mock()
    supervisor.target = Mock()
    supervisor.sockets = []
    replacement = Mock()
    with patch("denso_agent.windows_reload.get_subprocess", return_value=replacement):
        supervisor.restart()
    old.terminate.assert_called_once()
    old.kill.assert_called_once()
    assert all(
        call.kwargs.get("timeout") is not None for call in old.join.call_args_list
    )
    old.close.assert_called_once()
    replacement.start.assert_called_once()


def test_shutdown_releases_bound_socket():
    """Stopping the reload supervisor closes its listening sockets."""
    supervisor = object.__new__(WindowsReload)
    supervisor.process = Mock()
    supervisor.process.is_alive.return_value = False
    supervisor.sockets = [Mock()]
    supervisor.should_exit = Mock()
    supervisor.shutdown()
    supervisor.sockets[0].close.assert_called_once()


def test_unstoppable_worker_prevents_replacement_and_still_closes_sockets():
    """A failed bounded stop must not create two serving workers."""
    supervisor = object.__new__(WindowsReload)
    supervisor.process = Mock()
    supervisor.process.is_alive.return_value = True
    supervisor.sockets = [Mock()]
    supervisor.should_exit = Mock()
    with patch("denso_agent.windows_reload.get_subprocess") as replacement:
        with pytest.raises(RuntimeError, match="could not be stopped"):
            supervisor.restart()
        replacement.assert_not_called()
    with pytest.raises(RuntimeError, match="could not be stopped"):
        supervisor.shutdown()
    supervisor.sockets[0].close.assert_called_once()

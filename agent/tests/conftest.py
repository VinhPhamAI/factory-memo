"""Apply test selection safeguards without starting external resources."""

import os

import pytest

from denso_agent.event_loop import new_event_loop


def pytest_asyncio_loop_factories():
    """Use the same psycopg-compatible event loop as the native service.

    Returns:
        One named event-loop factory for all asynchronous tests.
    """
    return {"agent": new_event_loop}


def pytest_collection_modifyitems(config, items):
    """Prevent paid tests unless both explicit selection and environment opt-in exist.

    Args:
        config: Active pytest options including the marker selection expression.
        items: Collected tests that may include paid provider smoke tests.
    Raises:
        pytest.UsageError: A selected paid test lacks both required opt-ins.
    """
    selected_live = config.getoption("markexpr").strip() == "live"
    if selected_live and os.getenv("AGENT_RUN_LIVE") != "1":
        if any(item.get_closest_marker("live") for item in items):
            raise pytest.UsageError(
                "Paid tests require AGENT_RUN_LIVE=1 and explicit -m live."
            )


def pytest_runtest_setup(item):
    """Enforce both paid-call opt-ins even for broader marker expressions.

    Args:
        item: Selected test about to execute.
    Raises:
        pytest.UsageError: A live test lacks the exact paid-call opt-ins.
    """
    if item.get_closest_marker("live") and (
        item.config.getoption("markexpr").strip() != "live"
        or os.getenv("AGENT_RUN_LIVE") != "1"
    ):
        raise pytest.UsageError(
            "Paid tests require AGENT_RUN_LIVE=1 and explicit -m live."
        )

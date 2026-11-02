"""Decode complete SSE frames emitted by the controlled agent tests."""

import json


def parse_events(text: str) -> list[tuple[str, dict]]:
    """Decode event names and JSON data from a fully received agent response.

    Args:
        text: UTF-8 response text containing blank-line-separated SSE frames.

    Returns:
        Event names paired with their decoded data objects, in stream order.
    """
    events = []
    for frame in text.strip().split("\n\n"):
        if not frame:
            continue
        lines = frame.splitlines()
        name = next(
            line.removeprefix("event: ") for line in lines if line.startswith("event: ")
        )
        data = "\n".join(
            line.removeprefix("data: ") for line in lines if line.startswith("data: ")
        )
        events.append((name, json.loads(data)))
    return events

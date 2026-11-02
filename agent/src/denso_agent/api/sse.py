"""Serialize typed event data into UTF-8 Server-Sent Event frames."""

from pydantic import BaseModel


def encode_event(name: str, event: BaseModel) -> str:
    """Encode one complete frame with JSON-safe multiline text.

    Args:
        name: Public event name selected by the runtime.
        event: Validated public event payload.
    Returns:
        A complete SSE frame terminated by a blank line.
    """
    return f"event: {name}\ndata: {event.model_dump_json()}\n\n"

"""Check provider completion rules without network access."""

from types import SimpleNamespace
import asyncio

import pytest
from httpx import ReadError

from denso_agent.clients.modelark import MAX_ANSWER_CHARACTERS, ModelArk
from denso_agent.runtime.errors import AgentError


class Stream:
    """Supply deterministic SDK-shaped chunks and track closure."""

    def __init__(self, fragments, reason):
        """Store answer fragments and the final provider stop reason."""
        self.fragments = fragments
        self.reason = reason
        self.closed = False

    async def __aiter__(self):
        """Yield content followed by a finish marker."""
        for content in self.fragments:
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=content), finish_reason=None
                    )
                ]
            )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None), finish_reason=self.reason
                )
            ]
        )

    async def close(self):
        """Record explicit upstream resource release."""
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,code",
    [
        ("length", "output_limit"),
        ("content_filter", "filtered_output"),
        (None, "provider_error"),
        ("tool_calls", "provider_error"),
    ],
)
async def test_incomplete_answers_close_without_success(reason, code):
    """Reject incomplete finish reasons even when answer text arrived."""
    stream = Stream(["partial"], reason)
    emitted = []
    with pytest.raises(AgentError) as caught:
        await ModelArk.consume(stream, emitted.append)
    assert caught.value.code == code
    assert stream.closed
    assert emitted == [{"delta": "partial"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [MAX_ANSWER_CHARACTERS - 1, MAX_ANSWER_CHARACTERS])
async def test_answer_limit_allows_boundary(size):
    """Permit the full documented character budget across many fragments."""
    stream = Stream(["a"] * size, "stop")
    result = await ModelArk.consume(stream, lambda _: None)
    assert len(result) == size
    assert stream.closed


@pytest.mark.asyncio
async def test_answer_limit_checks_before_emission():
    """Keep the overflowing fragment out of both output and saved answer."""
    stream = Stream(["a" * MAX_ANSWER_CHARACTERS, "b"], "stop")
    emitted = []
    with pytest.raises(AgentError, match="output"):
        await ModelArk.consume(stream, emitted.append)
    assert len(emitted) == 1
    assert stream.closed


@pytest.mark.asyncio
async def test_empty_stop_is_not_success():
    """Require visible answer content before successful completion."""
    stream = Stream([], "stop")
    with pytest.raises(AgentError) as caught:
        await ModelArk.consume(stream, lambda _: None)
    assert caught.value.code == "empty_output"
    assert stream.closed


@pytest.mark.asyncio
async def test_cancellation_closes_blocked_provider_stream():
    """Close the upstream stream when a consumer cancels a blocked generation."""
    started = asyncio.Event()

    class BlockedStream(Stream):
        """Wait indefinitely after signalling that streaming has started."""

        async def __aiter__(self):
            """Block until cancellation without yielding answer fragments."""
            started.set()
            await asyncio.Event().wait()
            yield None

    stream = BlockedStream([], None)
    task = asyncio.create_task(ModelArk.consume(stream, lambda _: None))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


@pytest.mark.asyncio
async def test_network_failure_during_stream_is_provider_error():
    """Normalize an interrupted HTTP stream and close it after partial text."""

    class BrokenStream(Stream):
        """Fail with the transport exception used by the actual SDK stream."""

        async def __aiter__(self):
            """Raise a transport failure after the response has opened."""
            raise ReadError("private upstream address")
            yield None

    stream = BrokenStream([], None)

    async def create(**_):
        """Return the opened transport stream without making a network request."""
        return stream

    model = ModelArk(SimpleNamespace(model="test", max_output_tokens=10))
    model.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with pytest.raises(AgentError) as caught:
        await model.answer([], lambda _: None)
    assert caught.value.code == "provider_error"
    assert "private" not in caught.value.message
    assert stream.closed


@pytest.mark.asyncio
async def test_conflicting_finish_markers_never_turn_failure_into_success():
    """Reject a malformed provider stream that overwrites a filtered finish."""

    class ConflictingStream(Stream):
        """Supply incompatible finish markers for one completion choice."""

        async def __aiter__(self):
            """Send valid content and then conflicting terminal metadata."""
            async for chunk in super().__aiter__():
                yield chunk
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None), finish_reason="stop"
                    )
                ]
            )

    stream = ConflictingStream(["partial"], "content_filter")
    with pytest.raises(AgentError) as caught:
        await ModelArk.consume(stream, lambda _: None)
    assert caught.value.code == "provider_error"
    assert stream.closed

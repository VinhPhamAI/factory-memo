"""Direct ModelArk Chat Completions streaming with bounded answer storage."""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from httpx import HTTPError
from openai import APIError, APITimeoutError, AsyncOpenAI, BadRequestError

from denso_agent.runtime.errors import AgentError
from denso_agent.runtime.knowledge import ToolCall
from denso_agent.runtime.retrieval import SEARCH_TOOL

MAX_ANSWER_CHARACTERS = 32_000
# Reasoning beyond this is not forwarded; the answer still completes.
MAX_REASONING_CHARACTERS = 64_000
# Enough for one tool call with a short query.
ROUTING_MAX_TOKENS = 256
# Five bounded text fields (4,000 characters each) plus a title.
DRAFT_MAX_TOKENS = 4096
DRAFT_TOOL = {
    "type": "function",
    "function": {
        "name": "write_inquiry_draft",
        "description": "Write the draft inquiry fields from the conversation.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "situation": {"type": "string"},
                "observations": {"type": "string"},
                "attempted_actions": {"type": "string"},
                "proposed_fix": {"type": "string"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
}


@contextmanager
def _provider_errors() -> Iterator[None]:
    """Turn SDK and transport failures into public errors without provider details."""
    try:
        yield
    except APITimeoutError as exc:
        raise AgentError("timeout", "The provider request timed out.", True) from exc
    except BadRequestError as exc:
        # Inspect machine-readable codes only; never expose raw provider bodies.
        code = str(getattr(exc, "code", "") or "").lower()
        if code in {"context_length_exceeded", "context_window_exceeded"}:
            raise AgentError(
                "context_limit", "The model input exceeded its context limit."
            ) from exc
        raise AgentError(
            "provider_error", "The provider rejected the request."
        ) from exc
    except (APIError, HTTPError, ValueError) as exc:
        raise AgentError(
            "provider_error", "The provider request failed.", True
        ) from exc


class ModelArk:
    """Own the provider client and convert successful content into answer text."""

    def __init__(self, settings):
        """Retain runtime settings without opening network connections.

        Args:
            settings: Validated provider endpoint, secret and generation limits.
        """
        self.settings = settings
        self.client: AsyncOpenAI | None = None

    async def __aenter__(self):
        """Open the SDK client with retries disabled.

        Returns:
            This provider wrapper with an owned HTTP client.
        """
        self.client = AsyncOpenAI(
            api_key=self.settings.api_key.get_secret_value(),
            base_url=self.settings.base_url,
            max_retries=0,
            timeout=self.settings.request_timeout,
        )
        return self

    async def __aexit__(self, *_):
        """Close the owned client after shutdown or partial startup failure."""
        if self.client is not None:
            await self.client.close()

    async def answer(self, messages: list[dict], writer: Callable[[dict], Any]) -> str:
        """Stream standard provider content and return a fully finished answer.

        Args:
            messages: Bounded system/user/assistant input for Chat Completions.
            writer: Receives safe content deltas for graph custom streaming.

        Returns:
            Nonempty answer text after a successful stop marker.

        Raises:
            AgentError: Provider timeout, invalid output or request failure.
        """
        if self.client is None:
            raise RuntimeError("ModelArk client is not open")
        with _provider_errors():
            stream = await self.client.chat.completions.create(
                model=self.settings.model,
                messages=messages,
                stream=True,
                max_tokens=self.settings.max_output_tokens,
            )
            return await self.consume(stream, writer)

    async def route(self, messages: list[dict]) -> object:
        """Ask the model, without streaming, whether to call search_knowledge.

        The reply is private: its text is discarded and only the first
        `search_knowledge` call is used, so at most one search runs per turn.

        Args:
            messages: Routing instruction and bounded conversation context.

        Returns:
            None when the model answered without a tool call; otherwise the
            raw `query` argument, or "" for an unknown tool or malformed
            arguments. The caller validates the query.

        Raises:
            AgentError: Provider timeout or request failure.
        """
        calls = await self._request_calls(messages, [SEARCH_TOOL])
        if not calls:
            return None
        for call in calls:
            if call.function.name == "search_knowledge":
                return self._parse_call(call).arguments.get("query", "")
        return ""

    async def decide_tool(
        self, messages: list[dict], tools: list[dict]
    ) -> ToolCall | None:
        """Return one provider tool call, or mark malformed selections invalid.

        Args:
            messages: Current routing context, including any tool exchange.
            tools: Tool definitions offered for this decision.
        Returns:
            The parsed call, an invalid sentinel, or None for a direct reply.
        """
        calls = await self._request_calls(messages, tools)
        if not calls:
            return None
        if len(calls) != 1:
            return ToolCall("", "", {})
        return self._parse_call(calls[0])

    async def _request_calls(self, messages: list[dict], tools: list[dict]):
        """Ask the provider for a tool choice without streaming its private reply."""
        if self.client is None:
            raise RuntimeError("ModelArk client is not open")
        with _provider_errors():
            reply = await self.client.chat.completions.create(
                model=self.settings.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=ROUTING_MAX_TOKENS,
            )
        return reply.choices[0].message.tool_calls if reply.choices else None

    @staticmethod
    def _parse_call(call) -> ToolCall:
        """Parse one function argument object or return the invalid sentinel."""
        try:
            arguments = json.loads(call.function.arguments or "")
        except ValueError:
            return ToolCall("", "", {})
        if not isinstance(arguments, dict):
            return ToolCall("", "", {})
        return ToolCall(call.id, call.function.name, arguments)

    async def draft_inquiry(self, messages: list[dict]) -> dict | None:
        """Force one `write_inquiry_draft` tool call and return its arguments.

        Args:
            messages: Drafting instruction and bounded conversation.

        Returns:
            The parsed argument object, or None when the model made no such
            call or its arguments are not a JSON object. The caller validates
            the fields. Reply text is discarded.

        Raises:
            AgentError: Provider timeout, rejection (including a provider that
                does not support a pinned tool choice) or request failure.
        """
        if self.client is None:
            raise RuntimeError("ModelArk client is not open")
        with _provider_errors():
            reply = await self.client.chat.completions.create(
                model=self.settings.model,
                messages=messages,
                tools=[DRAFT_TOOL],
                tool_choice={
                    "type": "function",
                    "function": {"name": DRAFT_TOOL["function"]["name"]},
                },
                max_tokens=DRAFT_MAX_TOKENS,
            )
        calls = reply.choices[0].message.tool_calls if reply.choices else None
        for call in calls or []:
            if call.function.name != DRAFT_TOOL["function"]["name"]:
                continue
            try:
                arguments = json.loads(call.function.arguments or "")
            except ValueError:
                return None
            return arguments if isinstance(arguments, dict) else None
        return None

    @staticmethod
    async def consume(stream, writer: Callable[[dict], Any]) -> str:
        """Accumulate bounded content, close upstream and validate its finish marker.

        Args:
            stream: Open SDK stream yielding Chat Completion chunks.
            writer: Receives each accepted answer fragment as `{"delta": text}`
                and reasoning as `{"reasoning": text}`, before the next chunk.

        Returns:
            Completed visible answer. Reasoning text is sent to `writer` as
            `{"reasoning": text}` parts (at most MAX_REASONING_CHARACTERS) and is
            never part of the answer.

        Raises:
            AgentError: Empty, oversized, filtered or unfinished output.
        """
        fragments: list[str] = []
        reasoning_size = 0
        size = 0
        reason = None
        try:
            async for chunk in stream:
                for choice in chunk.choices:
                    if getattr(choice, "index", 0) != 0:
                        continue
                    reasoning = getattr(choice.delta, "reasoning_content", None)
                    if reasoning and reasoning_size < MAX_REASONING_CHARACTERS:
                        # Shown in the chat as "thinking"; never part of the answer.
                        reasoning = reasoning[
                            : MAX_REASONING_CHARACTERS - reasoning_size
                        ]
                        reasoning_size += len(reasoning)
                        writer({"reasoning": reasoning})
                    content = choice.delta.content
                    if content:
                        if reason is not None:
                            raise AgentError(
                                "provider_error",
                                "The provider sent content after completion.",
                            )
                        if size + len(content) > MAX_ANSWER_CHARACTERS:
                            raise AgentError(
                                "output_limit", "The answer exceeded the output limit."
                            )
                        size += len(content)
                        fragments.append(content)
                        writer({"delta": content})
                    if choice.finish_reason is not None:
                        if reason is not None and reason != choice.finish_reason:
                            raise AgentError(
                                "provider_error",
                                "The provider sent conflicting completion markers.",
                            )
                        reason = choice.finish_reason
        finally:
            await stream.close()
        if reason == "length":
            raise AgentError(
                "output_limit", "The provider stopped at its output limit."
            )
        if reason == "content_filter":
            raise AgentError("filtered_output", "The provider filtered the answer.")
        if reason != "stop":
            raise AgentError(
                "provider_error", "The provider did not finish the answer.", True
            )
        answer = "".join(fragments)
        if not answer.strip():
            raise AgentError(
                "empty_output", "The provider returned an empty answer.", True
            )
        return answer

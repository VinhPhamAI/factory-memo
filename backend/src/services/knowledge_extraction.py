"""Extract structured, reusable knowledge from raw conversations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import date
from enum import Enum
from typing import Any, Protocol

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError


DEFAULT_LLM_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/coding/v3"
RAW_CONVERSATION_PLACEHOLDER = "{RAW_CONVERSATION}"


class KnowledgeType(str, Enum):
    """Knowledge categories allowed by the extraction prompt."""

    FACT = "fact"
    RULE = "rule"
    CASE = "case"
    PROCEDURE = "procedure"
    HEURISTIC = "heuristic"


class KnowledgeItem(BaseModel):
    """One reusable knowledge statement extracted from a conversation."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    type: KnowledgeType
    valid_from: date | None = None
    valid_to: date | None = None


class KnowledgeExtractionResult(BaseModel):
    """Validated structured output returned by the service."""

    model_config = ConfigDict(extra="forbid")

    knowledge_items: list[KnowledgeItem] = Field(default_factory=list)


class SupportsInvoke(Protocol):
    """Small client interface used to keep the service easy to unit test."""

    def invoke(self, input: str) -> Any: ...


class KnowledgeExtractionError(RuntimeError):
    """Raised when the LLM response cannot be parsed or validated."""


class KnowledgeExtractionService:
    """LLM-backed service for turning a transcript into knowledge items.

    `prompt_template` is the knowledge base's extraction prompt; it must contain
    {RAW_CONVERSATION}, which is replaced by the transcript.
    """

    def __init__(self, client: SupportsInvoke, prompt_template: str):
        if RAW_CONVERSATION_PLACEHOLDER not in prompt_template:
            raise ValueError(
                f"Extraction prompt is missing its {RAW_CONVERSATION_PLACEHOLDER} placeholder"
            )
        self._client = client
        self._prompt_template = prompt_template

    @property
    def provenance(self) -> dict[str, Any]:
        """Non-secret settings identifying the extraction that produced a row."""
        details: dict[str, Any] = {
            "prompt_sha256": hashlib.sha256(self._prompt_template.encode("utf-8")).hexdigest(),
        }
        for name, attribute in (("model", "model_name"), ("max_tokens", "max_tokens")):
            value = getattr(self._client, attribute, None)
            if value is not None:
                details[name] = value
        thinking = (getattr(self._client, "extra_body", None) or {}).get("thinking")
        if isinstance(thinking, dict) and "type" in thinking:
            details["thinking"] = thinking["type"]
        response_format = (getattr(self._client, "model_kwargs", None) or {}).get("response_format")
        if response_format:
            details["response_format"] = response_format
        return details

    @classmethod
    def from_env(
        cls,
        prompt_template: str,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 2048,
        json_mode: bool = False,
        json_schema: bool = False,
    ) -> "KnowledgeExtractionService":
        """Build the OpenAI-compatible client configured by environment variables.

        EXTRACTION_* settings override the shared OPENROUTER_* ones, so bulk
        extraction can use another endpoint than the agent, e.g. a ModelArk
        batch inference endpoint or EXTRACTION_SERVICE_TIER=flex.
        """

        resolved_api_key = (
            api_key or os.getenv("EXTRACTION_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        )
        resolved_model = (
            model or os.getenv("EXTRACTION_MODEL") or os.getenv("OPENROUTER_MODEL")
        )
        resolved_base_url = (
            base_url
            or os.getenv("EXTRACTION_BASE_URL")
            or os.getenv("OPENROUTER_BASE_URL")
            or DEFAULT_LLM_BASE_URL
        )

        missing = []
        if not resolved_api_key:
            missing.append("OPENROUTER_API_KEY")
        if not resolved_model:
            missing.append("OPENROUTER_MODEL")
        if missing:
            raise ValueError(
                "Missing required LLM configuration: " + ", ".join(missing)
            )

        options: dict[str, Any] = {}
        if json_schema:
            options["model_kwargs"] = {"response_format": {"type": "json_schema", "json_schema": {
                "name": "knowledge_extraction", "schema": KnowledgeExtractionResult.model_json_schema(),
                "strict": False,
            }}}
        elif json_mode:
            options["model_kwargs"] = {"response_format": {"type": "json_object"}}
        # Provider-specific switch for ModelArk/DeepSeek. Leave it unset for
        # endpoints that do not accept a `thinking` request field.
        if thinking := os.getenv("EXTRACTION_THINKING", "").strip():
            if thinking not in {"enabled", "disabled"}:
                raise ValueError("EXTRACTION_THINKING must be enabled or disabled")
            options["extra_body"] = {"thinking": {"type": thinking}}
        if service_tier := os.getenv("EXTRACTION_SERVICE_TIER"):
            options["service_tier"] = service_tier
        if timeout := os.getenv("EXTRACTION_TIMEOUT_SECONDS"):
            try:
                options["timeout"] = float(timeout)
            except ValueError as exc:
                raise ValueError("EXTRACTION_TIMEOUT_SECONDS must be a number") from exc

        client = ChatOpenAI(
            model=resolved_model,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            temperature=0,
            streaming=False,
            max_tokens=max_tokens,
            **options,
        )
        return cls(client, prompt_template)

    def extract(self, raw_conversation: str) -> KnowledgeExtractionResult:
        """Extract and validate knowledge items from one raw conversation."""

        prompt = self._checked_prompt(raw_conversation)
        try:
            response = self._client.invoke(prompt)
        except Exception as exc:
            raise KnowledgeExtractionError(f"LLM request failed: {exc}") from exc
        return self._parse(response)

    async def aextract(self, raw_conversation: str) -> KnowledgeExtractionResult:
        """Async extract, so many conversations can be in flight at once.

        Clients without `ainvoke` run their blocking `invoke` in a thread.
        """

        prompt = self._checked_prompt(raw_conversation)
        ainvoke = getattr(self._client, "ainvoke", None)
        try:
            if ainvoke is not None:
                response = await ainvoke(prompt)
            else:
                response = await asyncio.to_thread(self._client.invoke, prompt)
        except Exception as exc:
            raise KnowledgeExtractionError(f"LLM request failed: {exc}") from exc
        return self._parse(response)

    def _checked_prompt(self, raw_conversation: str) -> str:
        if not raw_conversation or not raw_conversation.strip():
            raise ValueError("raw_conversation must not be empty")
        return self.build_prompt(raw_conversation)

    def _parse(self, response: Any) -> KnowledgeExtractionResult:
        metadata = getattr(response, "response_metadata", None) or {}
        usage = getattr(response, "usage_metadata", None) or {}
        token_usage = metadata.get("token_usage") or {}
        finish_reason = metadata.get("finish_reason")
        output_tokens = usage.get("output_tokens", token_usage.get("completion_tokens"))
        reasoning_tokens = (usage.get("output_token_details") or {}).get(
            "reasoning", (token_usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        )
        details = ", ".join(
            f"{key}={value}"
            for key, value in (
                ("finish_reason", finish_reason),
                ("output_tokens", output_tokens),
                ("reasoning_tokens", reasoning_tokens),
            )
            if value is not None
        )
        diagnostic = f" ({details})" if details else ""
        if finish_reason == "length":
            raise KnowledgeExtractionError(
                "The LLM hit its output token limit before completing knowledge extraction"
                + diagnostic
                + ". Reasoning tokens also consume the output budget. For supporting "
                "ModelArk/DeepSeek endpoints, set EXTRACTION_THINKING=disabled; "
                "otherwise reduce the input size or increase max_tokens."
            )
        content = self._response_text(response)
        if not content.strip():
            raise KnowledgeExtractionError("The LLM returned empty extraction text" + diagnostic)

        try:
            payload = json.loads(self._strip_json_fence(content))
        except json.JSONDecodeError as exc:
            raise KnowledgeExtractionError(
                "The LLM returned invalid knowledge extraction JSON"
                + diagnostic
                + f": {exc.msg} at line {exc.lineno}, column {exc.colno}"
            ) from exc
        try:
            return KnowledgeExtractionResult.model_validate(payload)
        except ValidationError as exc:
            # Keep diagnostics useful without logging extracted document contents.
            errors = exc.errors(include_input=False, include_context=False, include_url=False)
            summary = "; ".join(
                f"{'.'.join(map(str, error['loc'])) or '<root>'}: {error['type']}"
                for error in errors[:5]
            )
            raise KnowledgeExtractionError(
                "The LLM extraction JSON failed schema validation"
                + diagnostic
                + f": {summary}"
            ) from exc

    def build_prompt(self, raw_conversation: str) -> str:
        """Insert a transcript into the knowledge base's extraction prompt."""

        return self._prompt_template.replace(
            RAW_CONVERSATION_PLACEHOLDER, raw_conversation.strip(), 1
        )

    @staticmethod
    def _response_text(response: Any) -> str:
        if isinstance(response, str):
            return response

        content = getattr(response, "content", None)
        if isinstance(content, str):
            return content

        # LangChain may represent multimodal message content as typed blocks.
        if isinstance(content, list):
            text_parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if text_parts:
                return "".join(text_parts)

        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text
        raise KnowledgeExtractionError("The LLM response did not contain text")

    @staticmethod
    def _strip_json_fence(content: str) -> str:
        content = content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        return fenced.group(1) if fenced else content

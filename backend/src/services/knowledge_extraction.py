"""Extract structured, reusable knowledge from raw conversations."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from enum import Enum
from typing import Any, Protocol

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.src.prompts.extract import EXTRACT_KNOWLEDGE_PROMPT


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
    """LLM-backed service for turning a transcript into knowledge items."""

    def __init__(self, client: SupportsInvoke):
        self._client = client

    @classmethod
    def from_env(
        cls,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 2048,
    ) -> "KnowledgeExtractionService":
        """Build the OpenAI-compatible client configured by environment variables."""

        resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        resolved_model = model or os.getenv("OPENROUTER_MODEL")
        resolved_base_url = (
            base_url or os.getenv("OPENROUTER_BASE_URL") or DEFAULT_LLM_BASE_URL
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

        client = ChatOpenAI(
            model=resolved_model,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            temperature=0,
            streaming=False,
            max_tokens=max_tokens,
        )
        return cls(client)

    def extract(self, raw_conversation: str) -> KnowledgeExtractionResult:
        """Extract and validate knowledge items from one raw conversation."""

        if not raw_conversation or not raw_conversation.strip():
            raise ValueError("raw_conversation must not be empty")

        prompt = self.build_prompt(raw_conversation)
        try:
            response = self._client.invoke(prompt)
        except Exception as exc:
            raise KnowledgeExtractionError(f"LLM request failed: {exc}") from exc
        content = self._response_text(response)

        try:
            payload = json.loads(self._strip_json_fence(content))
            return KnowledgeExtractionResult.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            print(content)
            raise KnowledgeExtractionError(
                "The LLM returned invalid knowledge extraction JSON"
            ) from exc

    @staticmethod
    def build_prompt(raw_conversation: str) -> str:
        """Insert a transcript into the shared extraction prompt."""

        if RAW_CONVERSATION_PLACEHOLDER not in EXTRACT_KNOWLEDGE_PROMPT:
            raise RuntimeError("Extraction prompt is missing its conversation placeholder")
        return EXTRACT_KNOWLEDGE_PROMPT.replace(
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

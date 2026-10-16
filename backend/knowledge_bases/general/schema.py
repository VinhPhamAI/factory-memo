"""Typed extraction schema for general knowledge from any conversation.

One flat list of items, each a self-contained statement with its type, scope,
certainty and verbatim quotes. Sent to the LLM as the response JSON schema and
used to validate its output; to_result() maps it onto the shared knowledge
record, so storage, search and the agent tools are the same as for every KB.
Procedure steps are rendered into the claim here, never written twice by the LLM.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.src.services.knowledge_extraction import (
    KnowledgeExtractionResult,
    KnowledgeItem,
    KnowledgeType,
    StructuredExtraction,
)


class Certainty(str, Enum):
    """How the source presents an item; the extractor never upgrades it."""

    # Presented as true, decided or done by the speaker.
    STATED = "stated"
    # A hypothesis, estimate, suspicion, or unresolved disagreement.
    UNCERTAIN = "uncertain"
    # A proposal, intention or next step that has not happened yet.
    PLANNED = "planned"


class Quote(BaseModel):
    """A verbatim transcript excerpt supporting the item."""

    model_config = ConfigDict(extra="forbid")

    # Only when the transcript provides [T01]-style turn IDs; never invented.
    turn_id: str | None = None
    quote: str = Field(min_length=1)


class GeneralItem(BaseModel):
    """One reusable piece of knowledge stated in the conversation."""

    model_config = ConfigDict(extra="forbid")

    # Short label unique within one extraction, e.g. "case-demo-wifi", so
    # `based_on` names a case instead of counting positions in the list.
    key: str = Field(min_length=1)
    type: KnowledgeType
    subject: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    # Ordered steps of a procedure; null for every other type.
    steps: list[str] | None = None
    certainty: Certainty
    # The specific thing the item is limited to (product, system, team, site,
    # version, customer, ...); null when the source states no limit.
    applies_to: str | None = None
    # A short tag such as "after a release" or "for new hires", not prose.
    condition: str | None = None
    # Speaker label as written in the transcript; null when unattributed.
    speaker: str | None = None
    quotes: list[Quote] = Field(min_length=1)
    valid_from: date | None = None
    valid_to: date | None = None
    # Keys of the cases in `items` this item was drawn from.
    based_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_steps(self) -> "GeneralItem":
        if self.type is KnowledgeType.PROCEDURE and len(self.steps or []) < 2:
            raise ValueError("a procedure needs at least two steps")
        if self.type is not KnowledgeType.PROCEDURE and self.steps:
            raise ValueError(f"a {self.type.value} item has no steps")
        return self

    def to_item(self, positions: dict[str, int]) -> KnowledgeItem:
        context: dict = {"certainty": self.certainty.value}
        for key in ("applies_to", "condition", "speaker"):
            if value := getattr(self, key):
                context[key] = value
        context["quotes"] = [quote.model_dump(exclude_none=True) for quote in self.quotes]
        claim = self.claim.strip()
        if self.steps:
            context["steps"] = list(self.steps)
            claim += "\n" + "\n".join(f"{n}. {step}" for n, step in enumerate(self.steps, 1))
        return KnowledgeItem(
            subject=self.subject,
            claim=claim,
            context=context,
            type=self.type,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            supported_by=sorted({positions[key] for key in self.based_on}),
        )


class GeneralExtraction(StructuredExtraction):
    """Everything extracted from one conversation."""

    items: list[GeneralItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_based_on(self) -> "GeneralExtraction":
        types: dict[str, KnowledgeType] = {}
        for index, item in enumerate(self.items):
            if item.key in types:
                raise ValueError(f"items.{index}.key {item.key!r} is used by another item")
            types[item.key] = item.type
        for index, item in enumerate(self.items):
            if item.based_on and item.type is KnowledgeType.CASE:
                raise ValueError(f"items.{index} is a case and cannot be based_on others")
            for key in item.based_on:
                if key not in types:
                    raise ValueError(f"items.{index}.based_on names unknown key {key!r}")
                if types[key] is not KnowledgeType.CASE:
                    raise ValueError(f"items.{index}.based_on {key!r} is not a case item")
        return self

    def to_result(self) -> KnowledgeExtractionResult:
        positions = {item.key: index for index, item in enumerate(self.items)}
        return KnowledgeExtractionResult(
            knowledge_items=[item.to_item(positions) for item in self.items]
        )

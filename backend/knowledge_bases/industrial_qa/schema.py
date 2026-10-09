"""Typed Case Card and lesson extraction schema for the industrial QA benchmark."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.src.services.knowledge_extraction import (
    KnowledgeExtractionResult,
    KnowledgeItem,
    KnowledgeType,
    StructuredExtraction,
    scope_context,
)


class CaseEvidence(BaseModel):
    """A verbatim transcript excerpt supporting the case."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str | None = None
    quote: str = Field(min_length=1)


class CaseCard(BaseModel):
    """Context, observation, reasoning, action and outcome of one incident."""

    model_config = ConfigDict(extra="forbid")

    context: str
    observation: str
    reasoning: str
    action: str
    outcome: str | None = None
    evidence: list[CaseEvidence] = Field(min_length=1)


class IndustrialScope(BaseModel):
    """Where a case or lesson applies; null fields are not stored."""

    model_config = ConfigDict(extra="forbid")

    machine_id: str | None = None
    machine_model: str | None = None
    component_id: str | None = None
    component_model: str | None = None
    line_id: str | None = None
    station: str | None = None
    product_model: str | None = None
    configuration: str | None = None
    serial_range: str | None = None
    condition: str | None = None
    failure_mode: str | None = None


class IndustrialCase(BaseModel):
    """One incident whose retrieval claim is rendered from a Case Card."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    valid_from: date | None = None
    valid_to: date | None = None
    context: IndustrialScope = Field(default_factory=IndustrialScope)
    case_card: CaseCard

    def to_item(self) -> KnowledgeItem:
        return KnowledgeItem(
            subject=self.subject,
            claim=render_case_claim(self.case_card),
            context={**scope_context(self.context), "case_card": self.case_card.model_dump()},
            type=KnowledgeType.CASE,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
        )


class IndustrialLesson(BaseModel):
    """One reusable requirement, practice, heuristic, or ordered procedure."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    steps: list[str] | None = None
    mandatory: bool = False
    valid_from: date | None = None
    valid_to: date | None = None
    context: IndustrialScope = Field(default_factory=IndustrialScope)
    case_refs: list[int] = Field(default_factory=list)

    @property
    def knowledge_type(self) -> KnowledgeType:
        if self.mandatory:
            return KnowledgeType.RULE
        if self.steps:
            return KnowledgeType.PROCEDURE
        return KnowledgeType.HEURISTIC

    def to_item(self, case_positions: list[int]) -> KnowledgeItem:
        context = scope_context(self.context)
        claim = self.claim.strip()
        if self.steps:
            context["steps"] = list(self.steps)
            claim += "\n" + "\n".join(
                f"{number}. {step}" for number, step in enumerate(self.steps, 1)
            )
        return KnowledgeItem(
            subject=self.subject,
            claim=claim,
            context=context,
            type=self.knowledge_type,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            supported_by=case_positions,
        )


class IndustrialQaExtraction(StructuredExtraction):
    """Everything extracted from one benchmark conversation."""

    cases: list[IndustrialCase] = Field(default_factory=list)
    lessons: list[IndustrialLesson] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_case_refs(self) -> "IndustrialQaExtraction":
        for index, lesson in enumerate(self.lessons):
            if any(not 0 <= ref < len(self.cases) for ref in lesson.case_refs):
                raise ValueError(f"lessons.{index}.case_refs points to no case")
        return self

    def to_result(self) -> KnowledgeExtractionResult:
        return KnowledgeExtractionResult(knowledge_items=[
            *(case.to_item() for case in self.cases),
            *(lesson.to_item(sorted(set(lesson.case_refs))) for lesson in self.lessons),
        ])


def render_case_claim(card: CaseCard) -> str:
    """Build self-contained retrieval text from the structured card."""

    facets = [
        ("", card.context),
        ("Observation: ", card.observation),
        ("Reasoning: ", card.reasoning),
        ("Action: ", card.action),
        ("Outcome: ", card.outcome or "not stated"),
    ]
    return "\n".join(label + value.strip() for label, value in facets if value.strip())

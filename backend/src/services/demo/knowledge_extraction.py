"""Scripted PDF conversion and knowledge extraction for the prepared demo."""

from __future__ import annotations

import os
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.src.services.demo.scenario import DEMO_MODE_ENV
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage.models import KnowledgeRecord, KnowledgeStatus
from backend.src.storage.orm.base import new_id


ARTIFACT_PATH = Path(__file__).with_name("knowledge_extraction.json")
PDF_CONTENT_TYPE = "application/pdf"
_ENABLED = {"1", "true", "yes", "on"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoExtractionTiming(_Model):
    """Configurable waits used to make both worker stages visible in the UI."""

    conversion_seconds: float = Field(ge=0, le=120)
    extraction_seconds: float = Field(ge=0, le=120)


class DemoKnowledgeRecord(_Model):
    """One saved extraction result before fresh database IDs are assigned."""

    artifact_id: UUID
    subject: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    type: KnowledgeType
    context: dict[str, Any] = Field(default_factory=dict)
    stated_by: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    supported_by: list[UUID] = Field(default_factory=list)
    contradicted_by: list[UUID] = Field(default_factory=list)
    conflict_with: list[UUID] = Field(default_factory=list)
    superseded_by: UUID | None = None
    revision: dict[str, Any] = Field(default_factory=dict)
    references: list[dict[str, Any]] = Field(min_length=1)


class DemoKnowledgeExtraction(_Model):
    """Converted text and records replayed for one configured PDF title."""

    title: str = Field(min_length=1)
    timing: DemoExtractionTiming
    text: str = Field(min_length=1)
    records: list[DemoKnowledgeRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_links(self) -> "DemoKnowledgeExtraction":
        """Require unique artifact IDs and links that stay inside the artifact."""

        ids = [record.artifact_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("demo knowledge artifact IDs must be unique")
        known = set(ids)
        for record in self.records:
            links = [
                *record.supported_by,
                *record.contradicted_by,
                *record.conflict_with,
                *([record.superseded_by] if record.superseded_by else []),
            ]
            if unknown := [value for value in links if value not in known]:
                raise ValueError(
                    f"{record.artifact_id} links unknown demo records: {unknown}"
                )
        return self

    def matches(self, title: str, content_type: str) -> bool:
        """Return whether an uploaded PDF title selects this artifact."""

        return content_type == PDF_CONTENT_TYPE and _normalize(title) == _normalize(self.title)

    def instantiate(
        self, source_id: UUID
    ) -> tuple[list[KnowledgeRecord], dict[UUID, list[dict[str, Any]]]]:
        """Create fresh draft records and remap links to their new UUIDv7 IDs.

        Args:
            source_id: Uploaded source that will own every replayed record.

        Returns:
            Records in artifact order and their saved page references.
        """

        ids = {record.artifact_id: new_id() for record in self.records}
        records = []
        references = {}
        for saved in self.records:
            record = KnowledgeRecord(
                id=ids[saved.artifact_id],
                subject=saved.subject,
                claim=saved.claim,
                context=saved.context,
                type=saved.type,
                evidence_id=source_id,
                stated_by=saved.stated_by,
                valid_from=saved.valid_from,
                valid_to=saved.valid_to,
                supported_by=[ids[value] for value in saved.supported_by],
                contradicted_by=[ids[value] for value in saved.contradicted_by],
                conflict_with=[ids[value] for value in saved.conflict_with],
                superseded_by=ids.get(saved.superseded_by),
                status=KnowledgeStatus.DRAFT,
                revision=saved.revision,
            )
            records.append(record)
            references[record.id] = saved.references
        return records, references


def _normalize(value: str) -> str:
    """Normalize a configured or uploaded title for stable matching."""

    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


@lru_cache(maxsize=1)
def read_demo_knowledge_extraction() -> DemoKnowledgeExtraction:
    """Read and validate the saved extraction artifact regardless of demo mode."""

    return DemoKnowledgeExtraction.model_validate_json(
        ARTIFACT_PATH.read_text(encoding="utf-8")
    )


def load_demo_knowledge_extraction(
    title: str, content_type: str
) -> DemoKnowledgeExtraction | None:
    """Return the artifact for its configured PDF title when demo mode is enabled."""

    if os.getenv(DEMO_MODE_ENV, "").strip().lower() not in _ENABLED:
        return None
    artifact = read_demo_knowledge_extraction()
    return artifact if artifact.matches(title, content_type) else None

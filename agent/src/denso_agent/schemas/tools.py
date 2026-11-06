"""Bound the tool information shown in chat and kept with a saved answer."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

KnowledgeBaseName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,59}$")]
ToolName = Literal[
    "list_knowledge_bases",
    "search_knowledge",
    "create_inquiry_draft",
    "update_inquiry_draft",
]
# needs_choice: the conversation already has a draft, so nothing was created;
# not_draft: the inquiry was already submitted and cannot be edited.
ToolState = Literal[
    "running", "results", "empty", "unavailable", "needs_choice", "not_draft"
]


class CatalogEntry(BaseModel):
    """One active knowledge base from the backend catalog."""

    name: KnowledgeBaseName
    title: str = Field(max_length=200)
    description: str = Field(default="", max_length=240)


class SourceRef(BaseModel):
    """Reference that opens an authorized application source."""

    kb: KnowledgeBaseName
    source_id: UUID
    source_kind: Literal["upload", "conversation"]
    title: str = Field(max_length=200)
    reference: dict | None = None

    def without_quote(self) -> "SourceRef":
        """Keep the location while omitting evidence text from tool history."""
        if self.reference is None:
            return self
        reference = {
            key: value for key, value in self.reference.items() if key != "quote"
        }
        return self.model_copy(update={"reference": reference})


class ToolHit(BaseModel):
    """A ranked match projected for the chat UI."""

    rank: int = Field(ge=1)
    chunk_id: str = Field(max_length=64)
    title: str = Field(max_length=200)
    status: Literal["draft", "approved"]
    excerpt: str | None = Field(default=None, max_length=10000)
    sources: list[SourceRef] = Field(default_factory=list, max_length=2)


class ToolActivity(BaseModel):
    """One accepted KB or inquiry operation and its public result.

    Inquiry tools fill only `inquiry_id` and `title`; draft text is never kept.
    """

    id: str = Field(max_length=64)
    name: ToolName
    state: ToolState
    kb: KnowledgeBaseName | None = None
    query: str | None = Field(default=None, max_length=500)
    count: int | None = Field(default=None, ge=0)
    truncated: bool = False
    catalog: list[CatalogEntry] = Field(default_factory=list, max_length=50)
    results: list[ToolHit] = Field(default_factory=list, max_length=20)
    inquiry_id: UUID | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    title: str | None = Field(
        default=None, max_length=200, exclude_if=lambda value: value is None
    )

    def for_history(self) -> "ToolActivity":
        """Keep references and visible labels, omitting raw search excerpts."""
        return self.model_copy(
            update={
                "catalog": [
                    item.model_copy(update={"description": ""}) for item in self.catalog
                ],
                "results": [
                    item.model_copy(
                        update={
                            "excerpt": None,
                            "sources": [
                                source.without_quote() for source in item.sources
                            ],
                        }
                    )
                    for item in self.results
                ],
            }
        )

"""Public citation and search fields shared by completion events and history."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from denso_agent.schemas.tools import SourceRef

# not_used: no search this turn; results / empty: the search ran;
# unavailable: the search failed, timed out or the model's query was invalid.
SearchStatus = Literal["not_used", "results", "empty", "unavailable"]


class SearchScope(BaseModel):
    """Knowledge bases the calling service authorized for this request.

    Only the authenticated caller sets this; the model can never change it.
    """

    model_config = ConfigDict(extra="forbid")
    knowledge_bases: list[str] = Field(min_length=1, max_length=50)


class Citation(BaseModel):
    """Evidence that a marker such as [S1] in a saved answer refers to."""

    marker: str
    evidence_id: str
    title: str
    excerpt: str
    location: str | None = None
    sources: list[SourceRef] = Field(
        default_factory=list, max_length=2, exclude_if=lambda value: not value
    )

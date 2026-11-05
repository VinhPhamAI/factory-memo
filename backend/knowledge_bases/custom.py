"""Knowledge bases created at runtime, e.g. from the UI.

A created knowledge base copies a template's extraction and retrieval policy.
The application catalog rebuilds that policy from its database row.
"""

from __future__ import annotations

import dataclasses

from backend.knowledge_bases import general
from backend.knowledge_bases.base import KnowledgeBaseSpec
from backend.knowledge_bases.general.conflicts import conflict_detector

# Templates a created knowledge base may copy. Industrial QA depends on its
# equipment catalog, manuals and benchmark, so it is not offered as a template.
TEMPLATES: dict[str, KnowledgeBaseSpec] = {"general": general.SPEC}
DEFAULT_TEMPLATE = "general"
MAX_TITLE_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 500


def custom_spec(
    name: str, *, title: str, description: str = "", template: str = DEFAULT_TEMPLATE
) -> KnowledgeBaseSpec:
    """The spec of a created knowledge base; raises ValueError for bad fields."""

    try:
        base = TEMPLATES[template]
    except KeyError:
        raise ValueError(f"Unknown knowledge base template {template!r}") from None
    title = " ".join(title.split())
    description = description.strip()
    if not title or len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Title must be 1-{MAX_TITLE_LENGTH} characters")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"Description must be at most {MAX_DESCRIPTION_LENGTH} characters")
    # replace() re-runs validation, including the name pattern.
    return dataclasses.replace(
        base, name=name, title=title, description=description,
        conflict_factory=conflict_detector,
    )

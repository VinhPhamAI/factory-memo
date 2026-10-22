"""Equipment scope of a question, read from the catalog on the user's own words."""
from __future__ import annotations

import re

from backend.knowledge_bases.industrial_qa import CATALOG


def detected_scope(question: str):
    """(asset IDs, family) named by `question`; the family only when exactly one is implied."""
    assets = CATALOG.asset_ids(question)
    families = {CATALOG.family_of(asset) for asset in assets} | CATALOG.families_named(question)
    component = CATALOG.component(question)
    if component:
        families.add(CATALOG.components[component].family)
    if variant := CATALOG.machine_variant(question):
        families.add(variant[0])
    families.discard(None)
    return assets, next(iter(families)) if len(families) == 1 else None


def manual_query(question, family):
    """Manual search text: asset IDs, sites, lines, stations and cells removed, family prefixed."""
    text = CATALOG.without_sites(CATALOG.without_asset_ids(question))
    text = re.sub(r"\b(?:line|station)\s*\d+\b|\bcell\s+[A-Z]\b", " ", text, flags=re.I)
    return f"{family or ''} {re.sub(r'\s+', ' ', text).strip()}".strip()


def inherit_follow_up_scope(question, history):
    """A question without scope of its own inherits the latest scoped prior question."""
    if any(detected_scope(question)):
        return question
    for prior, _ in reversed(history):
        if any(detected_scope(prior)):
            return prior + "\nFollow-up question: " + question
    return question

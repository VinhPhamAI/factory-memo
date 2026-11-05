"""Conflict policy for application knowledge bases created from General."""

import os
from pathlib import Path

from backend.knowledge_bases.base import KnowledgeBaseSpec
from backend.src.services import llm
from backend.src.services.knowledge_conflicts import (
    CONFLICT_MAX_TOKENS,
    ConflictDetector,
    ConflictPolicy,
    ConflictSettings,
    Verdicts,
)


def conflict_detector(_spec: KnowledgeBaseSpec) -> ConflictDetector:
    """Create a scoped judge using the consolidation provider settings."""

    prompt_path = Path(__file__).parent / "prompts" / "conflict.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    return ConflictDetector(
        lambda: llm.chat_client_from_env(
            "CONSOLIDATION", max_tokens=CONFLICT_MAX_TOKENS,
            response_format=llm.json_schema_format("knowledge_conflicts", Verdicts),
        ),
        settings=ConflictSettings(),
        policy=ConflictPolicy(prompt, soft_keys=("applies_to", "condition")),
        model={
            "model": os.getenv("CONSOLIDATION_MODEL") or os.getenv("OPENROUTER_MODEL"),
            "max_tokens": CONFLICT_MAX_TOKENS,
            "thinking": os.getenv("CONSOLIDATION_THINKING") or None,
        },
    )

"""Definition of one knowledge base: its dataset, prompts, and database."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from backend.src.services.knowledge_extraction import RAW_CONVERSATION_PLACEHOLDER


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
EXTRACT_PROMPT_FILE = "extract.md"
AGENT_PROMPT_FILE = "agent.md"
DATABASE_PREFIX = "kb_"
# The database name kb_<name> must stay a plain PostgreSQL identifier.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


@dataclass(frozen=True)
class DatasetSpec:
    """Default ingestion input: a .csv (one transcript per row), .txt, or .md file."""

    path: Path
    transcript_column: str = "raw_conversation"
    stated_by_column: str = "stated_by"


@dataclass(frozen=True)
class KnowledgeBaseSpec:
    """Everything that differs between knowledge bases.

    Infrastructure (database server, LLM, embedding model) stays in the
    environment; each knowledge base gets its own database on that server.
    """

    name: str
    extract_prompt: str
    agent_prompt: str
    description: str = ""
    dataset: DatasetSpec | None = None
    # PostgreSQL full-text search configuration for lexical retrieval.
    text_search_config: str = "english"
    # None falls back to KNOWLEDGE_MIN_VECTOR_SIMILARITY.
    min_vector_similarity: float | None = None

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"Invalid knowledge base name {self.name!r}: use lowercase "
                "letters, digits, and underscores, starting with a letter"
            )
        if RAW_CONVERSATION_PLACEHOLDER not in self.extract_prompt:
            raise ValueError(
                f"Extraction prompt of {self.name!r} is missing "
                f"{RAW_CONVERSATION_PLACEHOLDER}"
            )
        if not self.agent_prompt.strip():
            raise ValueError(f"Agent prompt of {self.name!r} is empty")

    @property
    def database(self) -> str:
        return f"{DATABASE_PREFIX}{self.name}"

    @classmethod
    def from_directory(cls, directory: Path, **fields) -> "KnowledgeBaseSpec":
        """Load extract.md and agent.md from a knowledge base package directory."""

        return cls(
            name=fields.pop("name", directory.name),
            extract_prompt=(directory / EXTRACT_PROMPT_FILE).read_text(encoding="utf-8").strip(),
            agent_prompt=(directory / AGENT_PROMPT_FILE).read_text(encoding="utf-8").strip(),
            **fields,
        )

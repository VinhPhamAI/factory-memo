"""Read the existing backend KB API using only the current employee's access."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

from denso_agent.clients.backend import BackendError, request_json
from denso_agent.runtime.knowledge import Catalog
from denso_agent.runtime.retrieval import Evidence, RetrievalUnavailable
from denso_agent.schemas.tools import CatalogEntry, KnowledgeBaseName, SourceRef

CATALOG_LIMIT = 50
KB_NAME = TypeAdapter(KnowledgeBaseName)


class BackendSource(BaseModel):
    """Read the fields required to link a ranked chunk to its source."""

    source_id: UUID
    source_title: str
    source_kind: str
    original_url: str | None = None
    references: list[dict]


class BackendHit(BaseModel):
    """Read the ranked search fields the agent consumes."""

    chunk_id: UUID
    title: str
    content: str
    status: Literal["draft", "approved"]
    evidence: list[BackendSource]
    applicability: dict[str, str] | None = None


class BackendSearch(BaseModel):
    """Read only the search result list from the backend response."""

    results: list[BackendHit]


class BackendCatalog(BaseModel):
    """Read active catalog rows, excluding creation templates."""

    knowledge_bases: list[dict]


def _location(value: dict) -> dict | None:
    """Allow source locator fields and a short supporting quote for previews."""
    kind = value.get("type")
    quote = value.get("quote")
    preview = {"quote": quote[:500]} if isinstance(quote, str) and quote else {}
    if kind in ("page", "image"):
        if type(value.get("page")) is not int or value["page"] < 1:
            return None
        result = {"type": kind, "page": value["page"], **preview}
        bbox = value.get("bbox")
        if (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(type(item) in (int, float) and 0 <= item <= 1e6 for item in bbox)
        ):
            result["bbox"] = bbox
            for key in ("page_width", "page_height"):
                size = value.get(key)
                if type(size) in (int, float) and 0 < size <= 1e6:
                    result[key] = size
        return result
    if kind == "cells" and all(
        isinstance(value.get(key), str) and 0 < len(value[key]) <= 120
        for key in ("sheet", "range")
    ):
        return {
            "type": kind,
            "sheet": value["sheet"],
            "range": value["range"],
            **preview,
        }
    index_key = (
        {"paragraph": "index", "passage": "chunk_index"}.get(kind)
        if isinstance(kind, str)
        else None
    )
    if index_key and type(value.get(index_key)) is int and 0 <= value[index_key] <= 1e6:
        result = {"type": kind, index_key: value[index_key], **preview}
        if kind == "paragraph":
            table, cell = value.get("table"), value.get("cell")
            if type(table) is int and 0 < table <= 1e6 and isinstance(cell, str):
                result.update(table=table, cell=cell[:80])
        return result
    return None


def _location_text(reference: dict | None) -> str | None:
    """Describe a validated location in the text passed to the model."""
    if not reference:
        return None
    if "page" in reference:
        return f"Page {reference['page']}"
    if "sheet" in reference:
        return f"{reference['sheet']}!{reference['range']}"
    if "index" in reference:
        if "table" in reference:
            return f"Table {reference['table']}, cell {reference['cell']}"
        return f"Paragraph {reference['index']}"
    return f"Passage {reference['chunk_index']}"


class KnowledgeClient:
    """Reuse the caller-authorized catalog and ranked search API."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout: float,
        limit: int,
        excerpt_length: int,
    ):
        """Keep the lifespan-owned client and bounds for each operation."""
        self.http = http
        self.timeout = timeout
        self.limit = limit
        self.excerpt_length = excerpt_length

    async def _read(
        self, method: str, path: str, credential: SecretStr, response_type, **kwargs
    ):
        """Read one bounded JSON response, without following redirects."""
        try:
            return await request_json(
                self.http,
                method,
                path,
                credential,
                response_type,
                timeout=self.timeout,
                **kwargs,
            )
        except BackendError as exc:
            raise RetrievalUnavailable(str(exc)) from exc

    async def list(self, credential: SecretStr) -> Catalog:
        """Read the current employee's active KBs without caching permissions."""
        catalog = await self._read(
            "GET", "/v1/knowledge-bases", credential, BackendCatalog
        )
        rows = catalog.knowledge_bases
        try:
            entries = [
                CatalogEntry.model_validate(
                    {**item, "description": item.get("description", "")[:240]}
                )
                for item in rows[:CATALOG_LIMIT]
            ]
        except (TypeError, ValidationError) as exc:
            raise RetrievalUnavailable("Knowledge catalog was invalid.") from exc
        return Catalog(entries, truncated=len(rows) > CATALOG_LIMIT)

    async def search(
        self, credential: SecretStr, kb: str, query: str
    ) -> list[Evidence]:
        """Search one KB and return bounded chunks and validated source locators."""
        try:
            name = KB_NAME.validate_python(kb)
        except ValidationError as exc:
            raise RetrievalUnavailable("Knowledge base name was invalid.") from exc
        response = await self._read(
            "POST",
            f"/v1/knowledge-management/{name}/search",
            credential,
            BackendSearch,
            json={"query": query, "limit": self.limit},
        )
        return [self._evidence(name, hit) for hit in response.results[: self.limit]]

    def _evidence(self, name: str, hit: BackendHit) -> Evidence:
        """Map a backend hit without retaining raw URLs or scores."""
        sources = [
            SourceRef(
                kb=name,
                source_id=source.source_id,
                source_kind=(
                    "upload"
                    if source.original_url
                    and source.source_kind in {"document", "image"}
                    else "conversation"
                ),
                title=source.source_title[:200],
                reference=next(
                    (
                        location
                        for item in source.references
                        if (location := _location(item)) is not None
                    ),
                    None,
                ),
            )
            for source in hit.evidence[:2]
        ]
        return Evidence(
            id=str(hit.chunk_id),
            title=hit.title[:200],
            excerpt=hit.content[: self.excerpt_length],
            location=_location_text(sources[0].reference) if sources else None,
            status=hit.status,
            sources=tuple(sources),
            applicability={
                key: value[:240]
                for key, value in (hit.applicability or {}).items()
                if key in {"applies_to", "condition"} and value.strip()
            },
        )

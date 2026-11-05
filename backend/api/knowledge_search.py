"""Ranked chunk search of one knowledge base."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from backend.api.auth import require_current_user
from backend.api.knowledge_management import service
from backend.src.agents.rag.store import SearchMode
from backend.src.services.chunk_search import ChunkSearchResult
from backend.src.storage import StorageError
from backend.src.storage.embeddings import EmbeddingMismatch, EmbeddingUnavailable
from backend.src.storage.repositories import KnowledgeStorageUnavailable
from backend.src.storage.repositories.chunks import DOCUMENT_PASSAGE_KIND, KNOWLEDGE_CHUNK_KIND

router = APIRouter(
    prefix="/v1/knowledge-management",
    tags=["knowledge-management"],
    dependencies=[Depends(require_current_user)],
)

# The API calls vector search "dense"; the agent store calls it "semantic".
API_MODES = {
    "hybrid": SearchMode.HYBRID,
    "dense": SearchMode.SEMANTIC,
    "keyword": SearchMode.KEYWORD,
}


class SearchRequest(BaseModel):
    """Query and filters of a chunk search."""

    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["hybrid", "dense", "keyword"] = "hybrid"
    kinds: list[Literal["knowledge", "document_passage"]] = Field(
        default_factory=lambda: [KNOWLEDGE_CHUNK_KIND, DOCUMENT_PASSAGE_KIND], min_length=1
    )
    # Deprecated chunks are never searchable, so the value is rejected with 422.
    statuses: list[Literal["draft", "approved"]] = Field(
        default_factory=lambda: ["draft", "approved"], min_length=1
    )
    context: dict[str, str] = Field(default_factory=dict)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("query", mode="before")
    @classmethod
    def trimmed(cls, value: Any) -> Any:
        """Trim the query before the length rule so blank text is rejected."""

        return value.strip() if isinstance(value, str) else value


class SearchScores(BaseModel):
    """Score components of a hit; a component that did not rank it is null."""

    fused: float | None
    dense: float | None
    keyword: float | None


class SearchEvidence(BaseModel):
    """An active source of a chunk with the locations the chunk came from."""

    source_id: UUID
    source_title: str
    source_kind: str
    references: list[dict[str, Any]]
    original_url: str | None


class SearchResult(BaseModel):
    """One ranked chunk."""

    rank: int
    chunk_id: UUID
    citation: str
    kind: str
    status: str
    title: str
    content: str
    scores: SearchScores
    evidence: list[SearchEvidence]


class SearchResponse(BaseModel):
    """Ranked chunks for a query."""

    query: str
    mode: str
    embedding_model: str | None
    results: list[SearchResult]


def search_response(kb: str, body: SearchRequest, found: ChunkSearchResult) -> SearchResponse:
    """Build the public response from ranked hits and their evidence.

    Only fields the contract names are copied, so embeddings, search text and
    reviewer notes cannot leak through the hit objects.
    """

    results = [
        SearchResult(
            rank=rank,
            chunk_id=hit.chunk_id,
            citation=hit.citation,
            kind=hit.kind,
            status=hit.status,
            title=hit.title,
            content=hit.content,
            scores=SearchScores(fused=hit.fused, dense=hit.dense, keyword=hit.keyword),
            evidence=[
                SearchEvidence(
                    source_id=item.source_id,
                    source_title=item.source_title,
                    source_kind=item.source_kind,
                    references=item.references,
                    original_url=(
                        f"/v1/knowledge-management/{kb}/sources/upload/{item.source_id}/original"
                        if item.has_file
                        else None
                    ),
                )
                for item in hit.evidence
            ],
        )
        for rank, hit in enumerate(found.hits, start=1)
    ]
    return SearchResponse(
        query=body.query,
        mode=body.mode,
        embedding_model=found.embedding_model,
        results=results,
    )


@router.post("/{kb}/search", response_model=SearchResponse)
async def search_chunks(request: Request, kb: str, body: SearchRequest) -> SearchResponse:
    """Rank a knowledge base's searchable chunks for a query with their active evidence."""

    manager = await service(request, kb)
    knowledge_base_id, spec = manager.spec(kb)
    search = getattr(request.app.state, "chunk_search", None)
    if search is None:
        raise HTTPException(503, "Chunk search is unavailable")
    try:
        found = await search.search(
            knowledge_base_id,
            spec,
            body.query,
            mode=API_MODES[body.mode],
            kinds=body.kinds,
            statuses=body.statuses,
            context=body.context,
            limit=body.limit,
        )
    except EmbeddingMismatch as exc:
        raise HTTPException(409, str(exc)) from exc
    except EmbeddingUnavailable as exc:
        raise HTTPException(503, "The embedding provider is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (StorageError, KnowledgeStorageUnavailable) as exc:
        raise HTTPException(503, "Knowledge storage is unavailable") from exc
    return search_response(kb, body, found)

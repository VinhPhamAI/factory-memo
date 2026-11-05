"""Check that the KB callback uses the current caller and bounds backend data."""

import httpx
import pytest
from pydantic import SecretStr
from uuid import uuid4

from denso_agent.clients.knowledge import KnowledgeClient
from denso_agent.runtime.retrieval import RetrievalUnavailable
from denso_agent.schemas.tools import ToolActivity, ToolHit


async def test_callbacks_use_only_the_current_employee_and_backend_origin():
    requests = []

    def reply(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "knowledge_bases": [
                        {
                            "name": "press",
                            "title": "Press",
                            "description": "Hydraulics",
                            "private_field": "never forwarded",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(
        base_url="http://api:8005", transport=httpx.MockTransport(reply)
    ) as http:
        client = KnowledgeClient(http, timeout=1, limit=5, excerpt_length=200)
        catalog = await client.list(SecretStr("employee-one"))
        found = await client.search(SecretStr("employee-two"), "press", "E42")

    assert catalog.entries[0].model_dump() == {
        "name": "press",
        "title": "Press",
        "description": "Hydraulics",
    }
    assert found == []
    assert [(r.url.host, r.url.path, r.headers["Authorization"]) for r in requests] == [
        ("api", "/v1/knowledge-bases", "Bearer employee-one"),
        ("api", "/v1/knowledge-management/press/search", "Bearer employee-two"),
    ]


@pytest.mark.parametrize("status", [302, 401, 403, 404, 503])
async def test_refusals_and_redirects_are_unavailable(status):
    def reply(request):
        assert request.url.host == "api"
        return httpx.Response(status, headers={"Location": "http://elsewhere/private"})

    async with httpx.AsyncClient(
        base_url="http://api",
        transport=httpx.MockTransport(reply),
        follow_redirects=True,
    ) as http:
        client = KnowledgeClient(http, timeout=1, limit=5, excerpt_length=200)
        with pytest.raises(RetrievalUnavailable):
            await client.list(SecretStr("employee"))


async def test_ranked_source_keeps_location_and_discards_raw_response_fields():
    source_id = uuid4()

    def reply(request):
        assert request.url.path.endswith("/press/search")
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "chunk_id": str(uuid4()),
                        "title": "Manual",
                        "content": "x" * 250,
                        "status": "approved",
                        "applicability": {
                            "condition": "cold start",
                            "hidden": "private",
                        },
                        "evidence": [
                            {
                                "source_id": str(source_id),
                                "source_title": "Manual",
                                "source_kind": "document",
                                "original_url": "https://elsewhere/file",
                                "references": [
                                    {
                                        "type": "paragraph",
                                        "index": 4,
                                        "table": 1,
                                        "cell": "B2",
                                        "quote": "Supporting quote",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        base_url="http://api", transport=httpx.MockTransport(reply)
    ) as http:
        client = KnowledgeClient(http, timeout=1, limit=5, excerpt_length=200)
        [hit] = await client.search(SecretStr("employee"), "press", "E42")

    assert len(hit.excerpt) == 200 and hit.status == "approved"
    assert hit.applicability == {"condition": "cold start"}
    assert hit.sources[0].source_id == source_id
    assert hit.sources[0].source_kind == "upload"
    assert hit.sources[0].reference == {
        "type": "paragraph",
        "index": 4,
        "table": 1,
        "cell": "B2",
        "quote": "Supporting quote",
    }
    assert "elsewhere" not in repr(hit)
    activity = ToolActivity(
        id="search",
        name="search_knowledge",
        state="results",
        results=[
            ToolHit(
                rank=1,
                chunk_id=hit.id,
                title=hit.title,
                status="approved",
                sources=list(hit.sources),
            )
        ],
    )
    assert "Supporting quote" not in activity.for_history().model_dump_json()


async def test_response_body_is_bounded_before_parsing():
    def reply(_request):
        return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))

    async with httpx.AsyncClient(
        base_url="http://api", transport=httpx.MockTransport(reply)
    ) as http:
        client = KnowledgeClient(http, timeout=1, limit=5, excerpt_length=200)
        with pytest.raises(RetrievalUnavailable, match="exceeded"):
            await client.list(SecretStr("employee"))

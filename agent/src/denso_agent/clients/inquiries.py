"""Create, read and edit the employee's inquiry drafts through the backend API."""

from uuid import UUID

import httpx
from pydantic import BaseModel, SecretStr

from denso_agent.clients.backend import request_json


class BackendInquiry(BaseModel):
    """The inquiry fields the agent reads; other response fields are ignored."""

    id: UUID
    title: str
    status: str
    context: dict


class InquiryClient:
    """Call `/v1/inquiries` as the current employee."""

    def __init__(self, http: httpx.AsyncClient, *, timeout: float):
        """Keep the lifespan-owned client and the per-request time limit."""
        self.http = http
        self.timeout = timeout

    async def create(self, credential: SecretStr, body: dict) -> BackendInquiry:
        """Save a draft with `POST /v1/inquiries`.

        Raises:
            BackendError: The backend rejected the draft or could not be reached.
        """
        return await request_json(
            self.http,
            "POST",
            "/v1/inquiries",
            credential,
            BackendInquiry,
            timeout=self.timeout,
            json=body,
        )

    async def get(self, credential: SecretStr, inquiry_id: UUID) -> BackendInquiry:
        """Read one inquiry visible to the employee.

        Raises:
            BackendError: Not found (404), or the backend could not be reached.
        """
        return await request_json(
            self.http,
            "GET",
            f"/v1/inquiries/{inquiry_id}",
            credential,
            BackendInquiry,
            timeout=self.timeout,
        )

    async def update(
        self, credential: SecretStr, inquiry_id: UUID, body: dict
    ) -> BackendInquiry:
        """Change a draft's title or context with `PATCH /v1/inquiries/{id}`.

        Raises:
            BackendError: 409 when the inquiry is no longer a draft, other
                rejections, or the backend could not be reached.
        """
        return await request_json(
            self.http,
            "PATCH",
            f"/v1/inquiries/{inquiry_id}",
            credential,
            BackendInquiry,
            timeout=self.timeout,
            json=body,
        )

"""Send one bounded JSON request to the backend with the employee's credential."""

import asyncio

import httpx
from pydantic import SecretStr, ValidationError

MAX_RESPONSE_BYTES = 1024 * 1024


class BackendError(Exception):
    """A backend request failed; `status` is the HTTP status when one was received."""

    def __init__(self, message: str, status: int | None = None):
        """Store a safe message and the optional HTTP status."""
        super().__init__(message)
        self.status = status


async def request_json(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    credential: SecretStr,
    response_type,
    *,
    timeout: float,
    **kwargs,
):
    """Send one request and validate its bounded JSON body.

    Redirects are not followed, so the credential reaches only the configured
    backend.

    Args:
        http: Lifespan-owned client whose base URL is the backend.
        method: HTTP method.
        path: Backend path.
        credential: The current employee's access credential.
        response_type: Pydantic model that validates the response body.
        timeout: Seconds allowed for the whole request.
        **kwargs: Extra `httpx` request arguments such as `json`.
    Returns:
        The validated response model.
    Raises:
        BackendError: Non-2xx status (with `status`), oversized or invalid body,
            transport failure or timeout.
    """
    headers = {"Authorization": f"Bearer {credential.get_secret_value()}"}
    try:
        async with asyncio.timeout(timeout):
            async with http.stream(
                method, path, headers=headers, follow_redirects=False, **kwargs
            ) as response:
                if not response.is_success:
                    raise BackendError(
                        "Backend request was rejected.", response.status_code
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise BackendError("Backend response exceeded its limit.")
                    body.extend(chunk)
                return response_type.model_validate_json(body)
    except (httpx.HTTPError, TimeoutError, ValidationError) as exc:
        raise BackendError("Backend request could not be completed.") from exc

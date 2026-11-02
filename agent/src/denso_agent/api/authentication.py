"""Apply the shared trusted-service credential in one location."""

from secrets import compare_digest

from fastapi import HTTPException, Request


def authenticate(request: Request) -> None:
    """Reject requests that do not carry the configured service token.

    Args:
        request: Incoming request with runtime settings on its application.
    Raises:
        HTTPException: The authorization header is missing or incorrect.
    """
    expected = request.app.state.settings.api_token.get_secret_value()
    supplied = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not supplied.startswith(prefix) or not compare_digest(
        supplied[len(prefix) :].encode(), expected.encode()
    ):
        raise HTTPException(
            401, detail={"code": "unauthorized", "message": "Authentication required."}
        )

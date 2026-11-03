"""HTTP endpoints for signup, login, identity, refresh and logout.

Protected routes depend on `require_current_user`, which accepts only a bearer
access credential. The refresh credential travels only in an HttpOnly cookie
scoped to /auth. Auth writes from a browser must come from an allowed origin.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.api.auth_schemas import LoginRequest, PublicUser, SignupRequest, TokenResponse
from backend.src.services.auth import (
    Account,
    AuthService,
    AuthStorageUnavailable,
    ConflictingCredentials,
    InvalidCredentials,
    InvalidSession,
    IssuedTokens,
    UsernameTakenError,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
COOKIE_PATH = "/auth"
_bearer = HTTPBearer(auto_error=False)
_NOT_AUTHENTICATED = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class AuthHttpSettings:
    """Browser-facing auth settings.

    Attributes:
        allowed_origins: Frontend origins allowed to send auth writes. Requests
            without an Origin header (non-browser clients) are allowed.
        secure_cookies: Whether the refresh cookie has the Secure attribute.
    """

    allowed_origins: frozenset[str]
    secure_cookies: bool = True

    @classmethod
    def from_env(cls) -> AuthHttpSettings:
        """Read AUTH_ALLOWED_ORIGINS and AUTH_INSECURE_LOOPBACK_COOKIES.

        Raises:
            ValueError: Insecure cookies were enabled for a non-loopback origin.
        """

        origins = frozenset(
            origin.strip().rstrip("/")
            for origin in os.getenv("AUTH_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        )
        insecure = os.getenv("AUTH_INSECURE_LOOPBACK_COOKIES", "").strip().lower() in {"1", "true", "yes"}
        if insecure and not (origins and all(_is_loopback_origin(origin) for origin in origins)):
            raise ValueError(
                "AUTH_INSECURE_LOOPBACK_COOKIES is only allowed when every "
                "AUTH_ALLOWED_ORIGINS entry is a loopback http origin"
            )
        return cls(allowed_origins=origins, secure_cookies=not insecure)


def _is_loopback_origin(origin: str) -> bool:
    host = urlsplit(origin).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def get_auth_service(request: Request) -> AuthService:
    """Return the configured auth service, or fail with 503 when there is none."""

    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication is not configured")
    return service


def _settings(request: Request) -> AuthHttpSettings:
    return getattr(request.app.state, "auth_settings", None) or AuthHttpSettings(frozenset())


def check_origin(request: Request) -> None:
    """Reject auth writes sent by a browser page on a disallowed origin.

    The Origin header is compared with the configured frontend origins, not
    with the Host header, which a development proxy rewrites.
    """

    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in _settings(request).allowed_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin not allowed")


async def require_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Account:
    """Authenticate the bearer access credential of a protected request.

    Returns:
        The account with its current role, read from the database.

    Raises:
        HTTPException: 401 for a missing or unusable credential.
    """

    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers=_NOT_AUTHENTICATED)
    try:
        return await service.authenticate_access(credentials.credentials)
    except InvalidSession as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers=_NOT_AUTHENTICATED
        ) from exc


def auth_storage_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    """Report an unreachable application database without connection details."""

    # The cause's class only: messages may carry connection details or query values.
    logger.warning("Auth storage is unavailable: %s", type(_exc.__cause__ or _exc).__name__)
    return JSONResponse({"detail": "Authentication is temporarily unavailable"}, status_code=503)


async def auth_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Report invalid /auth input without echoing submitted values such as passwords.

    Other paths keep FastAPI's default 422 response.
    """

    if not request.url.path.startswith(f"{COOKIE_PATH}/"):
        return await request_validation_exception_handler(request, exc)
    detail = [
        {"type": error["type"], "loc": list(error["loc"]), "msg": error["msg"]}
        for error in exc.errors()
    ]
    return JSONResponse({"detail": detail}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


def _public(account: Account) -> PublicUser:
    return PublicUser(
        id=account.id, username=account.username, display_name=account.display_name, role=account.role
    )


def _token_response(request: Request, response: Response, tokens: IssuedTokens) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=tokens.refresh_expires_in,
        path=COOKIE_PATH,
        secure=_settings(request).secure_cookies,
        httponly=True,
        samesite="strict",
    )
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.expires_in)


ServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=PublicUser,
    dependencies=[Depends(check_origin)],
)
async def signup(body: Annotated[SignupRequest, Body()], service: ServiceDependency) -> PublicUser:
    """Create an active employee account; 409 when the username was ever used."""

    try:
        account = await service.signup(body.username, body.display_name, body.password)
    except UsernameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username is not available") from exc
    return _public(account)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(check_origin)])
async def login(
    body: Annotated[LoginRequest, Body()],
    request: Request,
    response: Response,
    service: ServiceDependency,
) -> TokenResponse:
    """Start a session; one generic 401 covers every credential failure."""

    try:
        tokens = await service.login(body.username, body.password)
    except InvalidCredentials as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password") from exc
    return _token_response(request, response, tokens)


@router.get("/me", response_model=PublicUser)
async def me(response: Response, account: Annotated[Account, Depends(require_current_user)]) -> PublicUser:
    """Return the signed-in account."""

    response.headers["Cache-Control"] = "no-store"
    return _public(account)


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(check_origin)])
async def refresh(request: Request, response: Response, service: ServiceDependency) -> TokenResponse:
    """Rotate the session named by the refresh cookie.

    A failed refresh sends no Set-Cookie, so a tab that lost a refresh race
    does not erase the cookie another tab just received.
    """

    cookie = request.cookies.get(REFRESH_COOKIE)
    if not cookie:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        tokens = await service.refresh(cookie)
    except InvalidSession as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated") from exc
    return _token_response(request, response, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(check_origin)])
async def logout(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    service: ServiceDependency,
) -> Response:
    """Revoke the current session and clear the refresh cookie; repeatable."""

    try:
        await service.logout(
            request.cookies.get(REFRESH_COOKIE) or None,
            credentials.credentials if credentials else None,
        )
    except ConflictingCredentials as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Credentials belong to different sessions"
        ) from exc
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        REFRESH_COOKIE,
        path=COOKIE_PATH,
        secure=_settings(request).secure_cookies,
        httponly=True,
        samesite="strict",
    )
    return response

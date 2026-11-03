"""Request and response bodies of the /auth endpoints."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints


def _trim_and_lowercase(value: object) -> object:
    """Normalize a username before its pattern is checked; non-strings fail later."""

    return value.strip().lower() if isinstance(value, str) else value


def _reject_control_characters(value: str) -> str:
    """Reject line breaks, NUL and other control characters in a shown name."""

    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("must not contain control characters")
    return value


SignupUsername = Annotated[
    str,
    BeforeValidator(_trim_and_lowercase),
    StringConstraints(pattern=r"^[a-z0-9_-]{3,32}$"),
]
DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
    AfterValidator(_reject_control_characters),
]
# Passwords are kept exactly as typed, including surrounding spaces.
SignupPassword = Annotated[str, Field(min_length=15, max_length=128)]

# Login accepts any existing account name and password within these bounds.
MAX_LOGIN_USERNAME_LENGTH = 256
MAX_LOGIN_PASSWORD_LENGTH = 1024


class SignupRequest(BaseModel):
    """New employee account. Roles cannot be chosen at signup."""

    model_config = ConfigDict(extra="forbid")

    username: SignupUsername
    display_name: DisplayName
    password: SignupPassword


class LoginRequest(BaseModel):
    """Password login for an existing account."""

    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=MAX_LOGIN_USERNAME_LENGTH)]
    password: Annotated[str, Field(min_length=1, max_length=MAX_LOGIN_PASSWORD_LENGTH)]


class PublicUser(BaseModel):
    """Account fields that may be shown to the signed-in user."""

    id: UUID
    username: str
    display_name: str
    role: str


class TokenResponse(BaseModel):
    """Bearer access credential; the refresh credential is only in the cookie."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int

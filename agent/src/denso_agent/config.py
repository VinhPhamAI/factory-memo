"""Validate runtime and provisioning settings without opening connections."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Validate native PostgreSQL connection strings without displaying passwords."""

    model_config = SettingsConfigDict(
        extra="ignore", populate_by_name=True, hide_input_in_errors=True
    )
    database_url: str = Field(alias="AGENT_DATABASE_URL", repr=False)

    @field_validator("database_url")
    @classmethod
    def native_database_url(cls, value: str) -> str:
        """Reject SQLAlchemy and empty connection strings.

        Args:
            value: PostgreSQL connection URL supplied through configuration.
        Returns:
            The unchanged native psycopg URL.
        Raises:
            ValueError: The URL does not use the native PostgreSQL scheme.
        """
        if not value.startswith("postgresql://"):
            raise ValueError("A native postgresql:// connection URL is required.")
        return value


class RuntimeSettings(DatabaseSettings):
    """Require service/provider credentials and bound generation resource use."""

    api_token: SecretStr = Field(alias="AGENT_API_TOKEN", min_length=1)
    api_key: SecretStr = Field(alias="MODELARK_API_KEY", min_length=1)
    base_url: str = Field(alias="MODELARK_BASE_URL", min_length=1)
    model: str = Field(alias="MODELARK_MODEL", min_length=1)
    request_timeout: float = Field(
        default=120, alias="AGENT_REQUEST_TIMEOUT_SECONDS", gt=0, le=3600
    )
    max_output_tokens: int = Field(
        default=1024, alias="AGENT_MAX_OUTPUT_TOKENS", gt=0, le=65536
    )
    max_concurrent_requests: int = Field(
        default=4, alias="AGENT_MAX_CONCURRENT_REQUESTS", ge=1, le=64
    )
    context_max_messages: int = Field(
        default=40, alias="AGENT_CONTEXT_MAX_MESSAGES", ge=1, le=1000
    )
    context_max_characters: int = Field(
        default=60000, alias="AGENT_CONTEXT_MAX_CHARACTERS", ge=1, le=1000000
    )
    history_max_messages: int = Field(
        default=200, alias="AGENT_HISTORY_MAX_MESSAGES", ge=1, le=10000
    )
    history_max_characters: int = Field(
        default=400000, alias="AGENT_HISTORY_MAX_CHARACTERS", ge=1, le=10000000
    )

    @field_validator("base_url")
    @classmethod
    def http_endpoint(cls, value: str) -> str:
        """Require an explicit HTTP endpoint, including local test endpoints.

        Args:
            value: Provider Chat Completions base URL.
        Returns:
            The validated endpoint.
        Raises:
            ValueError: The endpoint uses an unsupported scheme.
        """
        if not value.startswith(("https://", "http://")):
            raise ValueError("An HTTP or HTTPS provider endpoint is required.")
        return value

    @field_validator("api_token", "api_key")
    @classmethod
    def nonblank_secret(cls, value: SecretStr) -> SecretStr:
        """Reject secrets containing only whitespace.

        Args:
            value: Service token or provider key.
        Returns:
            The original secret without stripping meaningful characters.
        Raises:
            ValueError: No non-whitespace character is present.
        """
        if not value.get_secret_value().strip():
            raise ValueError("A nonblank secret is required.")
        return value


class SetupSettings(DatabaseSettings):
    """Require only the three database URLs needed for provisioning."""

    bootstrap_database_url: str = Field(
        alias="AGENT_BOOTSTRAP_DATABASE_URL", repr=False
    )
    setup_database_url: str = Field(alias="AGENT_SETUP_DATABASE_URL", repr=False)

    @field_validator("bootstrap_database_url", "setup_database_url")
    @classmethod
    def provisioning_database_url(cls, value: str) -> str:
        """Apply native URL validation to administrative connections.

        Args:
            value: Bootstrap or setup-owner PostgreSQL URL.
        Returns:
            The validated native connection string.
        Raises:
            ValueError: The connection scheme is unsupported.
        """
        return cls.native_database_url(value)

"""Request/response contracts for the V3 interactive browser foundation."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.utils.security_guard import validate_target_url


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _safe_origin(value: str) -> str:
    if not value:
        raise ValueError("target origin is required")
    parsed = urlparse(value)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("origins must not contain a path, query, or fragment")
    return validate_target_url(value, is_mock=True).rstrip("/")


class InteractiveSessionCreateRequest(_StrictModel):
    run_id: str | None = Field(default=None, max_length=80)
    organization_id: str = Field(default="default", min_length=1, max_length=120)
    owner_id: str = Field(default="operator", min_length=1, max_length=120)
    target_origin: str = Field(min_length=8, max_length=1024)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    checkpoint_id: str | None = Field(default=None, max_length=80)
    module_id: str | None = Field(default=None, max_length=160)
    route: str | None = Field(default=None, max_length=1024)
    ttl_seconds: int = Field(default=1800, ge=60, le=86400)

    @field_validator("target_origin")
    @classmethod
    def validate_target(cls, value: str) -> str:
        return _safe_origin(value)

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(_safe_origin(item) for item in value))


class InteractiveSessionActionRequest(_StrictModel):
    action: Literal["start", "pause", "resume", "continue", "retry", "complete", "cancel"]
    reason: str = Field(default="", max_length=500)


class InteractiveSessionHeartbeatRequest(_StrictModel):
    client_state: str = Field(default="connected", max_length=80)


class InteractiveEvidenceRequest(_StrictModel):
    """A locator-only event from a guided browser session.

    Values typed into the target are intentionally not accepted.  The client
    may send a short text fingerprint so a reviewer can correlate a control,
    but the raw text is never persisted.
    """

    event_type: Literal["click", "input", "select", "navigation", "assertion"]
    page_url: str = Field(min_length=8, max_length=2048)
    route: str = Field(default="", max_length=1024)
    label: str = Field(default="", max_length=255)
    selector: str = Field(default="", max_length=2048)
    xpath: str = Field(default="", max_length=2048)
    source: Literal["manual_interaction", "browser_observation", "assertion"] = "manual_interaction"
    text_content: str = Field(default="", max_length=2000)
    metadata: dict = Field(default_factory=dict)

    @field_validator("page_url")
    @classmethod
    def validate_page_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("page URL must not contain credentials or fragments")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("page URL must be an absolute HTTP(S) URL")
        return value.rstrip("/")


class InteractiveSessionResponse(_StrictModel):
    session_id: str
    status: str
    phase: str
    target_origin: str
    allowed_origins: list[str]
    expires_at: str
    last_activity_at: str
    stream_mode: str
    connection_token: str | None = None
    checkpoint_id: str | None = None
    module_id: str | None = None
    route: str | None = None
    evidence_count: int = 0
    metadata: dict = Field(default_factory=dict)

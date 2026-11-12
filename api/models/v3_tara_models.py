"""Validated request models for the additive OmniTest/Tara connector."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _safe_text(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > max_length or any(ord(ch) < 32 for ch in value):
        raise ValueError("text is empty, too long, or contains control characters")
    return value


class TaraConnectorConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connector_name: str = Field(..., min_length=1, max_length=80)
    tenant_id: str = Field(..., min_length=1, max_length=120)
    tenant_api_base_url: str = Field(..., min_length=1, max_length=2048)
    admin_api_base_url: str | None = Field(default=None, max_length=2048)
    result_callback_path: str = Field(default="/api/v1/omnitest/results", max_length=240)
    auth_mode: Literal["bearer", "header_secret", "oauth2", "none"] = "header_secret"
    secret_reference: str | None = Field(default=None, max_length=160)
    trigger_mode: Literal["webhook", "polling", "manual"] = "webhook"
    polling_interval_seconds: int = Field(default=60, ge=30, le=86400)

    _validate_text = field_validator("connector_name", "tenant_id", "secret_reference", mode="before")(
        lambda value: _safe_text(value, max_length=160)
    )

    @field_validator("result_callback_path")
    @classmethod
    def validate_callback_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/") or "://" in value or ".." in value or any(ord(ch) < 32 for ch in value):
            raise ValueError("result_callback_path must be a relative safe path")
        return value


class TaraTicketEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str = Field(..., min_length=1, max_length=120)
    ticket_id: str = Field(..., min_length=1, max_length=160)
    event_id: str = Field(..., min_length=1, max_length=160)
    event_type: str = Field(default="ticket.created", min_length=1, max_length=80)
    summary: str = Field(..., min_length=1, max_length=4000)
    affected_feature: str = Field(default="", max_length=500)
    expected_behavior: str = Field(default="", max_length=4000)
    actual_behavior: str = Field(default="", max_length=4000)
    target_origin: str = Field(default="", max_length=2048)
    authorized_role: str = Field(default="", max_length=160)
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    retry_limit: int = Field(default=1, ge=0, le=5)
    assessment_mode: Literal["functional", "security", "both"] = "functional"
    scenario_version: str = Field(default="unassigned", max_length=120)
    reporter_role: str = Field(default="user", max_length=120)

    _validate_fields = field_validator(
        "tenant_id", "ticket_id", "event_id", "event_type", "summary", "affected_feature",
        "expected_behavior", "actual_behavior", "target_origin", "authorized_role", "scenario_version", "reporter_role",
        mode="before",
    )(lambda value: _safe_text(value, max_length=4000) or "")

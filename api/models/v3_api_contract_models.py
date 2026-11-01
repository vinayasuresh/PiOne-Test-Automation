"""Strict, feature-gated request models for safe V3 API contract checks."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApiContractRegistrationRequest(_StrictModel):
    """A reviewed source document plus a trusted base URL.

    ``source_document`` is used only to normalize operations during registration;
    it is never persisted because source documents often contain examples with
    headers, API keys, or customer data.
    """

    name: str = Field(min_length=1, max_length=255)
    version: str = Field(default="unversioned", max_length=100)
    source_type: Literal["openapi", "postman"]
    base_url: str = Field(min_length=8, max_length=1024)
    environment: Literal["staging", "test", "production"] = "staging"
    approved: bool = False
    source_document: dict[str, Any]


class ApiOperationRule(_StrictModel):
    operation_key: str = Field(min_length=1, max_length=255)
    method: str = Field(default="GET", max_length=10)
    expected_status_codes: list[int] = Field(default_factory=lambda: [200], min_length=1, max_length=10)
    required_fields: list[str] = Field(default_factory=list, max_length=50)
    max_latency_ms: int = Field(default=3000, ge=1, le=60000)
    auth_requirement: Literal["none", "fixture", "required"] = "none"
    auth_fixture_ref: str = Field(default="", max_length=160)
    approved_role: str = Field(default="", max_length=120)
    allow_unauthenticated_probe: bool = False
    safe_read_only: bool = True

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        method = value.upper()
        if method not in _METHODS:
            raise ValueError("unsupported HTTP method")
        return method

    @field_validator("operation_key")
    @classmethod
    def validate_operation_key(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError("operation_key may contain letters, numbers, '.', '_', ':' and '-'")
        return value

    @field_validator("required_fields")
    @classmethod
    def validate_required_fields(cls, fields: list[str]) -> list[str]:
        for field in fields:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", field):
                raise ValueError("required field paths may contain only letters, numbers, '.', '_' and '-'")
        return fields


class ApiOperationConfigurationRequest(_StrictModel):
    """Reviewed assertion/auth metadata for one already registered operation."""

    expected_status_codes: list[int] = Field(default_factory=lambda: [200], min_length=1, max_length=10)
    required_fields: list[str] = Field(default_factory=list, max_length=50)
    max_latency_ms: int = Field(default=3000, ge=1, le=60000)
    auth_requirement: Literal["none", "fixture", "required"] = "none"
    auth_fixture_ref: str = Field(default="", max_length=160)
    approved_role: str = Field(default="", max_length=120)
    allow_unauthenticated_probe: bool = False
    safe_read_only: bool = True

    @field_validator("required_fields")
    @classmethod
    def validate_required_fields(cls, fields: list[str]) -> list[str]:
        return ApiOperationRule.validate_required_fields(fields)


class ApiOperationValidationRequest(_StrictModel):
    job_id: str | None = None
    run_id: str | None = None
    evidence_manifest_id: str | None = None
    scenario_version: str = Field(default="unassigned", max_length=100)
    negative_case: Literal["none", "unauthenticated"] = "none"

    @field_validator("job_id", "run_id", "evidence_manifest_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _ID_PATTERN.fullmatch(value):
            raise ValueError("linked IDs may contain only letters, numbers, '_' and '-'")
        return value

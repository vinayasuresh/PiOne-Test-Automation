"""Strict request contracts for feature-gated V3 dashboard-data checks."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_FIELD_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DataAssertionScenarioRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=255)
    route: str = Field(min_length=1, max_length=1024)
    ui_selector: str = Field(min_length=1, max_length=1024)
    metric_label: str = Field(default="", max_length=255)
    source_operation_id: str
    source_field: str
    source_timestamp_field: str = ""
    expected_filters: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    freshness_window_minutes: int = Field(default=60, ge=1, le=10080)
    tolerance: str = Field(default="0", max_length=80)
    value_format: Literal["number", "currency", "percentage"] = "number"
    approved: bool = False

    @field_validator("source_operation_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _ID_PATTERN.fullmatch(value):
            raise ValueError("source operation ID may contain only letters, numbers, '_' and '-'")
        return value

    @field_validator("source_field", "source_timestamp_field")
    @classmethod
    def validate_field_path(cls, value: str) -> str:
        if value and not _FIELD_PATTERN.fullmatch(value):
            raise ValueError("source field paths may contain only letters, numbers, '.', '_' and '-'")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("tolerance")
    @classmethod
    def validate_tolerance(cls, value: str) -> str:
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("tolerance must be a non-negative decimal") from exc
        if not parsed.is_finite() or parsed < 0:
            raise ValueError("tolerance must be a non-negative decimal")
        return value


class DataAssertionEvaluationRequest(_StrictModel):
    ui_value: str = Field(min_length=1, max_length=1024)
    filter_context: dict[str, Any] = Field(default_factory=dict)
    source_filter_context: dict[str, Any] = Field(default_factory=dict)
    job_id: str | None = None
    run_id: str | None = None
    evidence_manifest_id: str | None = None
    scenario_version: str = Field(default="unassigned", max_length=100)
    captured_at: datetime | None = None

    @field_validator("job_id", "run_id", "evidence_manifest_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _ID_PATTERN.fullmatch(value):
            raise ValueError("linked IDs may contain only letters, numbers, '_' and '-'")
        return value

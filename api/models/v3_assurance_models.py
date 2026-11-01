"""Feature-flagged request contracts for the additive V3 assurance core."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class V3EvidenceLinks(_StrictModel):
    run_id: str | None = None
    execution_id: str | None = None
    checkpoint_id: str | None = None
    artifact_id: str | None = None

    @field_validator("run_id", "execution_id", "checkpoint_id", "artifact_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _ID_PATTERN.fullmatch(value):
            raise ValueError("linked IDs may contain only letters, numbers, '_' and '-'")
        return value


class V3IncidentIntakeRequest(_StrictModel):
    correlation_id: str = Field(min_length=4, max_length=160)
    ticket_reference: str | None = Field(default=None, max_length=160)
    grouping_key: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=4000)
    affected_feature: str = Field(default="", max_length=255)
    reporter_role: str = Field(default="", max_length=120)
    expected_behavior: str = Field(default="", max_length=4000)
    actual_behavior: str = Field(default="", max_length=4000)
    priority: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    scenario_version: str = Field(default="unassigned", max_length=100)
    target: str = Field(default="", max_length=1024)
    authorized_role: str = Field(default="", max_length=120)
    mode: Literal["functional_only", "security_only", "both"] = "functional_only"
    retry_limit: int = Field(default=0, ge=0, le=5)
    links: V3EvidenceLinks = Field(default_factory=V3EvidenceLinks)


class V3JobTransitionRequest(_StrictModel):
    state: Literal["queued", "running", "completed", "failed", "blocked", "inconclusive", "cancelled"]
    verdict: Literal["pending", "reproduced", "not_reproduced", "blocked", "inconclusive"] | None = None
    technical_category: str = Field(default="", max_length=100)


class V3AttemptRequest(_StrictModel):
    worker_id: str = Field(default="", max_length=120)
    state: Literal["created", "running", "completed", "failed", "blocked", "inconclusive"] = "created"
    terminal_outcome: Literal["pending", "reproduced", "not_reproduced", "blocked", "inconclusive", "failed"] = "pending"
    failure_category: str = Field(default="", max_length=100)
    event_sequence: list[dict[str, str]] = Field(default_factory=list, max_length=100)


class V3EvidenceManifestRequest(_StrictModel):
    artifact_id: str | None = None
    run_id: str | None = None
    source_step: str = Field(default="", max_length=160)
    source_route: str = Field(default="", max_length=1024)
    checksum: str = Field(default="", max_length=128)
    content_type: str = Field(default="application/octet-stream", max_length=120)
    storage_location: str = Field(default="", max_length=1024)

    @field_validator("artifact_id", "run_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _ID_PATTERN.fullmatch(value):
            raise ValueError("linked IDs may contain only letters, numbers, '_' and '-'")
        return value

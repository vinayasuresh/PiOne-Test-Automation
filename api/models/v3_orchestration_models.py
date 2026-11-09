"""Strict request models for approved V3 orchestration and helpdesk records."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssuranceScenarioRequest(_StrictModel):
    name: str = Field(min_length=3, max_length=255)
    feature: str = Field(default="", max_length=255)
    environment: Literal["test", "staging", "production"] = "staging"
    authorized_role: str = Field(default="", max_length=120)
    target_origin: str = Field(min_length=8, max_length=1024)
    scenario_type: Literal["api_contract", "data_assertion"]
    reference_id: str = Field(min_length=1, max_length=80)
    max_actions: int = Field(default=3, ge=1, le=10)
    is_approved: bool = False


class V3OrchestrateRequest(_StrictModel):
    scenario_id: str = Field(min_length=1, max_length=80)
    worker_id: str = Field(default="v3-bounded-runner", max_length=120)


class V3RetryRequest(_StrictModel):
    worker_id: str = Field(default="v3-bounded-runner", max_length=120)


class AssignmentRuleRequest(_StrictModel):
    product: str = Field(default="OmniTest by PiOne", max_length=255)
    feature: str = Field(default="", max_length=255)
    service: str = Field(default="", max_length=255)
    owner_team: str = Field(min_length=2, max_length=255)
    severity_threshold: Literal["Low", "Medium", "High", "Critical"] = "Low"
    escalation_window_minutes: int = Field(default=0, ge=0, le=10080)
    is_enabled: bool = True


class AssignmentRequest(_StrictModel):
    owner_team: str = Field(min_length=2, max_length=255)
    next_action: str = Field(min_length=2, max_length=500)


class MonitoringScheduleRequest(_StrictModel):
    name: str = Field(min_length=3, max_length=255)
    scenario_id: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=8, max_length=1024)
    environment: Literal["test", "staging", "production"] = "staging"
    grouping_key: str = Field(min_length=1, max_length=255)
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    is_enabled: bool = True


class ManualMonitoringTriggerRequest(_StrictModel):
    """Human-initiated check from a saved definition; never a scheduler tick."""

    authorized_role: str = Field(default="", max_length=120)
    expected_behavior: str = Field(default="The approved critical journey is available.", max_length=4000)
    actual_behavior: str = Field(default="Manual monitoring verification requested by an operator.", max_length=4000)

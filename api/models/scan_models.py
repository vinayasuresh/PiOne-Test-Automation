"""Pydantic request and response models for the /scan API."""

import re
from typing import Any, Optional

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    mock: bool = False
    interactive_mode: bool = False
    interactive_timeout: int = 300  # seconds the crawler waits per route confirmation

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url is required")
        if not re.match(r"^https?://", v, re.IGNORECASE):
            raise ValueError("url must start with http:// or https://")
        return v.rstrip("/")


# ---------------------------------------------------------------------------
# Response — inner structures
# ---------------------------------------------------------------------------


class ComponentGroups(BaseModel):
    """Components on a single route, grouped by category.

    All fields are lists of human-readable labels so the UI can render them
    directly. `tables`, `charts`, and `maps` are lists (not counts) — the
    summary still exposes their total counts.
    """

    buttons:   list[str] = []
    inputs:    list[str] = []
    dropdowns: list[str] = []
    tables:    list[str] = []
    charts:    list[str] = []
    maps:      list[str] = []


class Interaction(BaseModel):
    """A single user-demonstrated interaction captured in interactive mode."""

    type:      str   # "click" | "input" | "select"
    component: str   # "button" | "input" | "dropdown"
    label:     str
    selector:  str = ""
    tag:       str = ""
    detected_type: str = ""
    framework_type: str = ""
    component_tag: str = ""
    page_url: str = ""
    value: str = ""


class RouteResult(BaseModel):
    path: str
    menu_name: str = ""
    purpose: str = ""
    framework: str = ""
    components: ComponentGroups
    interactions: list[Interaction] = []
    automation_targets: list[dict[str, Any]] = []


class ScanSummary(BaseModel):
    total_routes:  int
    total_buttons: int
    total_inputs:  int
    total_tables:  int
    scan_duration_seconds: float


# ---------------------------------------------------------------------------
# Top-level response
# ---------------------------------------------------------------------------


class ScanResponse(BaseModel):
    """Top-level scan response.

    `status` may be one of:
        - "success"           full scan completed
        - "partial_success"   scan failed mid-flight; routes collected so far
        - "timeout"           scan exceeded the timeout budget
        - "error"             scan could not start
    """

    status: str = "success"
    routes:  list[RouteResult]
    summary: ScanSummary
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    status: str = "error"
    detail: str


# ---------------------------------------------------------------------------
# Test automation workflow models
# ---------------------------------------------------------------------------


class GenerateTestRequest(BaseModel):
    base_url: str
    scan: ScanResponse
    description: Optional[str] = None
    feature_focus: Optional[str] = None


class GeneratedTestResponse(BaseModel):
    status: str = "success"
    script: str
    execution_command: str


class ExecuteTestRequest(BaseModel):
    base_url: str
    scan: ScanResponse
    script: str


class ExecutionResultRow(BaseModel):
    TC_ID: str
    Screen: str
    Module: str
    Feature: str
    Test_Case: str
    Expected_Result: str
    Actual_Result: str
    Status: str
    Failure_Info: str = ""
    Bug_Priority: str = ""


class ExecutionSummary(BaseModel):
    total: int
    passed: int
    failed: int
    skipped: int = 0


class ExecuteTestResponse(BaseModel):
    status: str = "success"
    rows: list[ExecutionResultRow]
    summary: ExecutionSummary


class SystemInfoResponse(BaseModel):
    os: str
    browser: str
    python_version: str
    environment: str

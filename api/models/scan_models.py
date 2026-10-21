"""Pydantic request and response models for the /scan API."""

import re
from typing import Any, Optional

from pydantic import BaseModel, field_validator


from api.utils.security_guard import validate_target_url


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
    resume_run_id: Optional[str] = None
    scan_id: Optional[str] = None
    selected_module_ids: Optional[list[str]] = None
    auth_session_id: Optional[str] = None
    run_id: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return validate_target_url(v)

    @field_validator("resume_run_id", "run_id")
    @classmethod
    def validate_resume_run_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("ID may contain only letters, numbers, '_' and '-' (max 80)")
        return value

    @field_validator("scan_id", "auth_session_id")
    @classmethod
    def validate_scan_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("ID may contain only letters, numbers, '_' and '-' (max 80)")
        return value

    @field_validator("selected_module_ids")
    @classmethod
    def validate_selected_module_ids(cls, values: Optional[list[str]]) -> Optional[list[str]]:
        if values is None:
            return None
        cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("selected_module_ids must not contain duplicates")
        if len(cleaned) > 200:
            raise ValueError("selected_module_ids may contain at most 200 items")
        return cleaned


class ModuleDiscoveryRequest(BaseModel):
    """Authenticate once and return only the current application's modules."""

    url: str
    username: Optional[str] = None
    password: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_discovery_url(cls, value: str) -> str:
        return validate_target_url(value)


class ModuleOption(BaseModel):
    id: str
    module_id: Optional[str] = None
    label: str
    route: str = ""
    source: str = ""
    hierarchy_level: int = 0
    parent_module: Optional[str] = None
    confidence: float = 1.0

    def model_post_init(self, __context: Any) -> None:
        if not self.module_id:
            self.module_id = self.id


class ModuleDiscoveryResponse(BaseModel):
    run_id: str
    auth_session_id: str
    modules: list[ModuleOption]
    message: Optional[str] = None
    correlation_id: Optional[str] = None


class RouteReplayRequest(BaseModel):
    base_url: str
    route: str
    checkpoint_id: str
    username: Optional[str] = None
    password: Optional[str] = None
    scan_id: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return validate_target_url(value)

    @field_validator("checkpoint_id", "scan_id")
    @classmethod
    def validate_replay_ids(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("IDs may contain only letters, numbers, '_' and '-' (max 80)")
        return value

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
    route_status: str = "WORKING"
    route_score: int = 0
    dom_stable: bool = True
    page_load_success: bool = True
    redirected: bool = False
    api_response_present: bool = False
    failed_api_responses: int = 0
    interaction_success_rate: float = 0.0


class ScanSummary(BaseModel):
    total_routes:  int
    total_buttons: int
    total_inputs:  int
    total_dropdowns: int = 0
    total_tables:  int
    working_routes: int = 0
    partial_routes: int = 0
    non_working_routes: int = 0
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
    run_id: Optional[str] = None
    routes:  list[RouteResult]
    summary: ScanSummary
    message: Optional[str] = None
    checkpoint_id: Optional[str] = None
    replay_comparison: Optional[dict[str, Any]] = None
    route_namespace: Optional[str] = None
    # Opaque authenticated browser-session reference for later execution.
    # Never contains cookies or credentials.
    auth_session_id: Optional[str] = None


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
    context_id: Optional[str] = None
    provider: str = "deterministic"
    model: Optional[str] = None
    user_prompt: Optional[str] = None
    run_id: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def validate_generate_base_url(cls, value: str) -> str:
        return validate_target_url(value)

    @field_validator("context_id")
    @classmethod
    def validate_context_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("context_id may contain only letters, numbers, '_' and '-' (max 80)")
        return value


class TestContextResponse(BaseModel):
    context_id: str
    requirements_characters: int = 0
    testcase_characters: int = 0
    user_prompt_characters: int = 0
    source_files: list[str] = []


class LLMProviderInfo(BaseModel):
    id: str
    label: str
    available: bool
    default_model: str = ""
    detail: str = ""


class GeneratedTestResponse(BaseModel):
    status: str = "success"
    run_id: Optional[str] = None
    script: str
    execution_command: str
    planning_notes: str = ""
    context_id: Optional[str] = None
    test_cases: list["TestCase"] = []
    planning_metadata: dict[str, Any] = {}
    coverage_gaps: list[dict[str, Any]] = []
    provider: str = "deterministic"
    model: str = ""
    fallback_used: bool = False
    warnings: list[str] = []


class TestStep(BaseModel):
    __test__ = False
    step_id: str
    action: str
    route: str = ""
    target_label: str = ""
    selector: dict[str, Any] = {}
    expected_result: str = ""
    source: str = "scan"
    value: str = ""
    element_identity: str = ""
    safety_classification: str = ""


class TestCase(BaseModel):
    __test__ = False
    tc_id: str
    title: str
    module: str
    route: str
    purpose: str = ""
    requirement_references: list[str] = []
    steps: list[TestStep] = []
    tags: list[str] = []
    priority: str = "Medium"
    prerequisites: list[str] = []
    locators: dict[str, Any] = {}
    safety_classification: str = "safe_action"
    latest_result: str = "Untested"
    last_execution_time: Optional[str] = None
    baseline_version: str = "1.0"


class ExecuteTestRequest(BaseModel):
    base_url: str
    scan: ScanResponse
    script: str
    test_cases: list[TestCase] = []
    allow_mutating_actions: bool = False
    # Authenticated scan session used to open the same application context
    # during execution. This is an opaque server-side state-file ID.
    auth_session_id: Optional[str] = None
    run_id: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def validate_execute_base_url(cls, value: str) -> str:
        return validate_target_url(value)

    @field_validator("auth_session_id")
    @classmethod
    def validate_auth_session_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("auth_session_id contains invalid characters")
        return value


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
    Step_ID: str = ""
    Action: str = ""
    Artifact_Path: str = ""
    locator_used: str = ""
    failure_category: str = ""
    console_errors: list[str] = []
    network_errors: list[str] = []
    element_identity: str = ""
    safety_classification: str = ""


class ExecutionSummary(BaseModel):
    total: int
    passed: int
    failed: int
    skipped: int = 0


class ExecuteTestResponse(BaseModel):
    status: str = "success"
    run_id: Optional[str] = None
    rows: list[ExecutionResultRow]
    summary: ExecutionSummary
    artifacts: dict[str, str] = {}


class RerunStepRequest(BaseModel):
    base_url: str
    test_case: TestCase
    step_id: str
    auth_session_id: Optional[str] = None
    run_id: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def validate_rerun_base_url(cls, value: str) -> str:
        return validate_target_url(value)


class RerunStepResponse(BaseModel):
    status: str = "success"
    step_id: str
    locator_used: str = ""
    actual_result: str = ""
    failure_info: str = ""
    artifact_path: str = ""
    console_errors: list[str] = []
    network_errors: list[str] = []


class RefreshSessionRequest(BaseModel):
    run_id: str
    url: str
    username: Optional[str] = None
    password: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_refresh_url(cls, value: str) -> str:
        return validate_target_url(value)


class RefreshSessionResponse(BaseModel):
    status: str = "success"
    run_id: str
    auth_session_id: str
    message: str = "Session refreshed successfully"


class SessionStatusResponse(BaseModel):
    run_id: str
    auth_session_id: Optional[str] = None
    is_valid: bool
    status: str  # "authenticated" | "expired" | "missing"
    detail: str


class RegressionRunRequest(BaseModel):
    execution_id: Optional[str] = None
    base_url: str
    run_id: Optional[str] = None

    @field_validator("execution_id")
    @classmethod
    def validate_execution_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not re.fullmatch(r"execution_[0-9]{8}_[0-9]{6}(?:_[A-Za-z0-9_-]+)?", value):
            raise ValueError("Invalid execution artifact ID")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_regression_base_url(cls, value: str) -> str:
        return validate_target_url(value)


class RegressionRunResponse(BaseModel):
    status: str = "success"
    run_id: str
    execution_id: str
    summary: ExecutionSummary
    rows: list[ExecutionResultRow]
    diff: dict[str, Any] = {}
    artifacts: dict[str, str] = {}


class SystemInfoResponse(BaseModel):
    os: str
    browser: str
    python_version: str
    environment: str


class ScanControlRequest(BaseModel):
    run_id: str
    action: str  # "pause" | "resume" | "continue" | "retry" | "end"


class ScanControlResponse(BaseModel):
    status: str = "success"
    run_id: str
    action: str
    message: str = ""


class ArtifactStatusResponse(BaseModel):
    run_id: str
    status: str  # "ready" | "in_progress" | "not_found"
    artifacts: dict[str, bool] = {}
    download_urls: dict[str, str] = {}


class CreateRunRequest(BaseModel):
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    allowed_scope: str = "/"
    interactive_mode: bool = False
    selected_module_ids: list[str] = []

    @field_validator("url")
    @classmethod
    def validate_create_run_url(cls, value: str) -> str:
        return validate_target_url(value)


class SaveProgressRequest(BaseModel):
    run_id: str
    state: Optional[dict[str, Any]] = None


class ResumeRunRequest(BaseModel):
    run_id: str
    username: Optional[str] = None
    password: Optional[str] = None
    rescan_modules: list[str] = []


class CreateRegressionSuiteRequest(BaseModel):
    name: str
    base_url: str
    environment: str = "production"
    description: str = ""
    baseline_run_id: Optional[str] = None
    test_case_ids: list[str] = []
    filter_tags: list[str] = []
    filter_modules: list[str] = []

    @field_validator("base_url")
    @classmethod
    def validate_suite_url(cls, value: str) -> str:
        return validate_target_url(value)


class UpdateRegressionSuiteRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    environment: Optional[str] = None
    case_toggles: dict[str, bool] = {}
    filter_tags: list[str] = []
    filter_modules: list[str] = []


class ExecuteSelectedRegressionRequest(BaseModel):
    run_id: str
    base_url: str
    suite_id: Optional[str] = None
    filter_type: str = "all"  # all, modules, routes, tags, failed_only, changed_only, never_executed, review_required
    selected_test_case_ids: list[str] = []
    selected_modules: list[str] = []
    selected_routes: list[str] = []
    selected_tags: list[str] = []
    failed_only: bool = False
    changed_since_baseline_only: bool = False
    never_executed_only: bool = False
    manual_review_only: bool = False
    confirmed: bool = False
    expected_test_count: Optional[int] = None
    expected_step_count: Optional[int] = None
    auth_session_id: Optional[str] = None
    allow_mutating_actions: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_exec_url(cls, value: str) -> str:
        return validate_target_url(value)


class SaveBaselineRequest(BaseModel):
    run_id: str
    name: str = "Baseline Plan"
    version: str = "1.0"
    confirmed_overwrite: bool = False


class CompareBaselineRequest(BaseModel):
    current_run_id: str
    baseline_run_id: str


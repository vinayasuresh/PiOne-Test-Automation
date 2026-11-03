"""api/db/models.py: SQLAlchemy declarative models for PiOne Testmate.

Defines schemas for:
- projects, application_targets
- scan_runs, scan_modules, scan_routes, scan_elements, route_edges, checkpoints
- test_plans, test_cases
- regression_suites, regression_suite_cases
- executions, execution_steps
- artifacts, audit_events
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from api.db.session import Base


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _gen_id() -> str:
    return uuid4().hex


# ---------------------------------------------------------------------------
# 1. Projects and Application Targets
# ---------------------------------------------------------------------------

class Project(Base):
    __tablename__ = "projects"

    id = Column(String(80), primary_key=True, default=_gen_id)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=_now_utc, nullable=False)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    targets = relationship("ApplicationTarget", back_populates="project", cascade="all, delete-orphan")


class ApplicationTarget(Base):
    __tablename__ = "application_targets"

    id = Column(String(80), primary_key=True, default=_gen_id)
    project_id = Column(String(80), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False, default="Default Application")
    base_url = Column(String(1024), nullable=False, index=True)
    allowed_scope = Column(String(1024), default="/", nullable=False)
    environment = Column(String(80), default="production", nullable=False)
    created_at = Column(DateTime, default=_now_utc, nullable=False)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    project = relationship("Project", back_populates="targets")
    runs = relationship("ScanRun", back_populates="target")


# ---------------------------------------------------------------------------
# 2. Scan Runs, Modules, Routes, Elements, Edges, Checkpoints
# ---------------------------------------------------------------------------

class ScanRun(Base):
    __tablename__ = "scan_runs"

    id = Column(String(80), primary_key=True)  # run_id
    application_target_id = Column(String(80), ForeignKey("application_targets.id", ondelete="SET NULL"), nullable=True)
    base_url = Column(String(1024), nullable=False, index=True)
    allowed_path_namespace = Column(String(1024), default="/", nullable=False)
    status = Column(String(50), default="initialized", nullable=False, index=True)  # initialized, scanning, paused, completed, partial_scanned, failed
    auth_status = Column(String(50), default="none", nullable=False)  # authenticated, expired, none
    username = Column(String(255), nullable=True)  # Redacted/username only, no password
    selected_modules = Column(JSON, default=list)
    discovered_modules = Column(JSON, default=list)
    execution_summary = Column(JSON, default=dict)
    progress_pct = Column(Float, default=0.0)
    is_archived = Column(Boolean, default=False, nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False, index=True)

    target = relationship("ApplicationTarget", back_populates="runs")
    modules = relationship("ScanModule", back_populates="run", cascade="all, delete-orphan")
    routes = relationship("ScanRoute", back_populates="run", cascade="all, delete-orphan")
    elements = relationship("ScanElement", back_populates="run", cascade="all, delete-orphan")
    checkpoints = relationship("Checkpoint", back_populates="run", cascade="all, delete-orphan")
    executions = relationship("Execution", back_populates="run", cascade="all, delete-orphan")
    audit_events = relationship("AuditEvent", back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_scan_runs_url_status", "base_url", "status"),
        Index("ix_scan_runs_created", "created_at"),
    )


class ScanModule(Base):
    __tablename__ = "scan_modules"

    id = Column(String(80), primary_key=True, default=_gen_id)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    module_id = Column(String(120), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    route = Column(String(1024), nullable=True)
    status = Column(String(50), default="pending")  # pending, scanning, completed, failed
    error = Column(Text, nullable=True)
    routes_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    run = relationship("ScanRun", back_populates="modules")


class ScanRoute(Base):
    __tablename__ = "scan_routes"

    id = Column(String(80), primary_key=True, default=_gen_id)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    path = Column(String(1024), nullable=False, index=True)
    menu_name = Column(String(255), default="")
    purpose = Column(Text, default="")
    status = Column(String(50), default="WORKING", index=True)  # WORKING, PARTIAL, NON-WORKING
    score = Column(Integer, default=100)
    dom_stable = Column(Boolean, default=True)
    page_load_success = Column(Boolean, default=True)
    redirected = Column(Boolean, default=False)
    failed_api_responses = Column(Integer, default=0)
    target_count = Column(Integer, default=0)
    diagnostic_signals = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    run = relationship("ScanRun", back_populates="routes")

    __table_args__ = (
        Index("ix_scan_routes_run_path", "run_id", "path"),
    )


class ScanElement(Base):
    __tablename__ = "scan_elements"

    id = Column(String(80), primary_key=True, default=_gen_id)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    route = Column(String(1024), nullable=False, index=True)
    element_identity = Column(String(512), nullable=False, index=True)
    role = Column(String(100), default="")
    label = Column(String(512), default="")
    selector = Column(JSON, default=dict)
    category = Column(String(100), default="")
    safety_classification = Column(String(100), default="unknown")
    quality_score = Column(Integer, default=50)
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    run = relationship("ScanRun", back_populates="elements")


class RouteEdge(Base):
    __tablename__ = "route_edges"

    id = Column(String(80), primary_key=True, default=_gen_id)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    from_route = Column(String(1024), nullable=False)
    to_route = Column(String(1024), nullable=False)
    action_type = Column(String(100), default="navigation")
    label = Column(String(255), default="")
    created_at = Column(DateTime, default=_now_utc, nullable=False)


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id = Column(String(80), primary_key=True, default=_gen_id)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)  # Optimistic concurrency lock
    namespace = Column(String(1024), default="")
    current_module = Column(String(255), default="")
    current_route = Column(String(1024), default="")
    visited_routes = Column(JSON, default=list)
    pending_routes = Column(JSON, default=list)
    completed_routes = Column(JSON, default=list)
    failed_routes = Column(JSON, default=list)
    partial_routes = Column(JSON, default=list)
    scroll_position = Column(JSON, default=dict)
    state_data = Column(JSON, default=dict)
    schema_version = Column(String(20), default="2.0")
    created_at = Column(DateTime, default=_now_utc, nullable=False)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    run = relationship("ScanRun", back_populates="checkpoints")

    __table_args__ = (
        Index("ix_checkpoints_run_version", "run_id", "version"),
    )


# ---------------------------------------------------------------------------
# 3. Test Plans, Test Cases, Regression Suites
# ---------------------------------------------------------------------------

class TestPlan(Base):
    __tablename__ = "test_plans"

    id = Column(String(80), primary_key=True, default=_gen_id)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False, default="Generated Test Plan")
    version = Column(String(50), default="1.0")
    is_baseline = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    cases = relationship("TestCaseModel", back_populates="plan", cascade="all, delete-orphan")


class TestCaseModel(Base):
    __tablename__ = "test_cases"

    id = Column(String(80), primary_key=True, default=_gen_id)
    plan_id = Column(String(80), ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id = Column(String(80), nullable=False, index=True)
    tc_id = Column(String(100), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    module = Column(String(255), default="General", index=True)
    route = Column(String(1024), nullable=False, index=True)
    tags = Column(JSON, default=list)  # ["smoke", "regression", "checkout", "navigation", "security"]
    priority = Column(String(50), default="Medium")
    prerequisites = Column(JSON, default=list)
    locators = Column(JSON, default=dict)
    steps = Column(JSON, default=list)
    safety_classification = Column(String(100), default="safe_action")
    latest_result = Column(String(50), default="Untested")  # Pass, Fail, Skipped, Untested
    last_execution_time = Column(DateTime, nullable=True)
    baseline_version = Column(String(50), default="1.0")
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    plan = relationship("TestPlan", back_populates="cases")

    __table_args__ = (
        Index("ix_test_cases_run_tcid", "run_id", "tc_id"),
    )


class RegressionSuite(Base):
    __tablename__ = "regression_suites"

    id = Column(String(80), primary_key=True, default=_gen_id)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, default="")
    application_target_id = Column(String(80), ForeignKey("application_targets.id", ondelete="SET NULL"), nullable=True)
    base_url = Column(String(1024), nullable=False, index=True)
    environment = Column(String(80), default="production")
    baseline_run_id = Column(String(80), nullable=True, index=True)
    filter_tags = Column(JSON, default=list)
    filter_modules = Column(JSON, default=list)
    is_archived = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=_now_utc, nullable=False)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    suite_cases = relationship("RegressionSuiteCase", back_populates="suite", cascade="all, delete-orphan")


class RegressionSuiteCase(Base):
    __tablename__ = "regression_suite_cases"

    id = Column(String(80), primary_key=True, default=_gen_id)
    suite_id = Column(String(80), ForeignKey("regression_suites.id", ondelete="CASCADE"), nullable=False, index=True)
    test_case_id = Column(String(80), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    is_enabled = Column(Boolean, default=True, nullable=False)
    tags = Column(JSON, default=list)
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    suite = relationship("RegressionSuite", back_populates="suite_cases")


# ---------------------------------------------------------------------------
# 4. Executions, Execution Steps, Artifacts, Audit Events
# ---------------------------------------------------------------------------

class Execution(Base):
    __tablename__ = "executions"

    id = Column(String(80), primary_key=True)  # execution_id
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    suite_id = Column(String(80), ForeignKey("regression_suites.id", ondelete="SET NULL"), nullable=True, index=True)
    baseline_id = Column(String(80), nullable=True)
    status = Column(String(50), default="completed", nullable=False, index=True)
    total_steps = Column(Integer, default=0)
    passed_steps = Column(Integer, default=0)
    failed_steps = Column(Integer, default=0)
    skipped_steps = Column(Integer, default=0)
    diff_summary = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    run = relationship("ScanRun", back_populates="executions")
    steps = relationship("ExecutionStep", back_populates="execution", cascade="all, delete-orphan")


class ExecutionStep(Base):
    __tablename__ = "execution_steps"

    id = Column(String(80), primary_key=True, default=_gen_id)
    execution_id = Column(String(80), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False, index=True)
    tc_id = Column(String(100), nullable=False)
    step_id = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)
    element_identity = Column(String(512), default="")
    locator_used = Column(String(512), default="")
    status = Column(String(50), nullable=False)  # Pass, Fail, Skipped
    actual_result = Column(Text, default="")
    failure_info = Column(Text, default="")
    failure_category = Column(String(100), default="")
    console_errors = Column(JSON, default=list)
    network_errors = Column(JSON, default=list)
    artifact_path = Column(String(1024), default="")
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    execution = relationship("Execution", back_populates="steps")


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(String(80), primary_key=True, default=_gen_id)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    execution_id = Column(String(80), nullable=True, index=True)
    artifact_type = Column(String(50), nullable=False, index=True)  # json, yaml, excel, markdown, checkpoint, screenshots
    file_path = Column(String(1024), nullable=False)
    file_size = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now_utc, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(String(80), primary_key=True, default=_gen_id)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)

    run = relationship("ScanRun", back_populates="audit_events")


# ---------------------------------------------------------------------------
# 5. V3 Client Assurance (feature-flagged, additive)
# ---------------------------------------------------------------------------

class SupportIncident(Base):
    """Sanitized support signal.  It intentionally never stores chat transcripts."""

    __tablename__ = "support_incidents"

    id = Column(String(80), primary_key=True, default=_gen_id)
    correlation_id = Column(String(160), nullable=False, unique=True, index=True)
    ticket_reference = Column(String(160), nullable=True, index=True)
    grouping_key = Column(String(255), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    affected_feature = Column(String(255), default="")
    reporter_role = Column(String(120), default="")
    expected_behavior = Column(Text, default="")
    actual_behavior = Column(Text, default="")
    priority = Column(String(30), default="Medium", nullable=False)
    status = Column(String(40), default="open", nullable=False, index=True)
    owner_team = Column(String(255), nullable=False, default="Unassigned", index=True)
    next_action = Column(String(500), nullable=False, default="Review verification outcome")
    last_outcome = Column(String(40), nullable=False, default="pending")
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    jobs = relationship("VerificationJob", back_populates="incident", cascade="all, delete-orphan")


class VerificationJob(Base):
    """A durable, bounded verification request linked to V2 evidence by IDs."""

    __tablename__ = "verification_jobs"

    id = Column(String(80), primary_key=True, default=_gen_id)
    incident_id = Column(String(80), ForeignKey("support_incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    execution_id = Column(String(80), ForeignKey("executions.id", ondelete="SET NULL"), nullable=True, index=True)
    checkpoint_id = Column(String(80), ForeignKey("checkpoints.id", ondelete="SET NULL"), nullable=True, index=True)
    artifact_id = Column(String(80), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True)
    scenario_id = Column(String(80), ForeignKey("assurance_scenarios.id", ondelete="SET NULL"), nullable=True, index=True)
    scenario_version = Column(String(100), nullable=False, default="unassigned")
    target = Column(String(1024), nullable=False, default="")
    authorized_role = Column(String(120), nullable=False, default="")
    mode = Column(String(60), nullable=False, default="functional_only")
    state = Column(String(40), nullable=False, default="created", index=True)
    verdict = Column(String(40), nullable=False, default="pending", index=True)
    technical_category = Column(String(100), default="")
    grouping_key = Column(String(255), nullable=False, index=True)
    retry_limit = Column(Integer, nullable=False, default=0)
    policy_decision = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    incident = relationship("SupportIncident", back_populates="jobs")
    attempts = relationship("VerificationAttempt", back_populates="job", cascade="all, delete-orphan")
    evidence_manifests = relationship("EvidenceManifest", back_populates="job", cascade="all, delete-orphan")
    scenario = relationship("AssuranceScenario", back_populates="jobs")


class VerificationAttempt(Base):
    __tablename__ = "verification_attempts"

    id = Column(String(80), primary_key=True, default=_gen_id)
    job_id = Column(String(80), ForeignKey("verification_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    worker_id = Column(String(120), default="")
    state = Column(String(40), nullable=False, default="created")
    terminal_outcome = Column(String(40), default="pending")
    failure_category = Column(String(100), default="")
    event_sequence = Column(JSON, default=list)
    started_at = Column(DateTime, default=_now_utc, nullable=False)
    ended_at = Column(DateTime, nullable=True)

    job = relationship("VerificationJob", back_populates="attempts")

    __table_args__ = (UniqueConstraint("job_id", "attempt_number", name="uq_verification_attempt_job_number"),)


class EvidenceManifest(Base):
    __tablename__ = "evidence_manifests"

    id = Column(String(80), primary_key=True, default=_gen_id)
    job_id = Column(String(80), ForeignKey("verification_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_id = Column(String(80), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    source_step = Column(String(160), default="")
    source_route = Column(String(1024), default="")
    checksum = Column(String(128), nullable=False, default="")
    content_type = Column(String(120), nullable=False, default="application/octet-stream")
    storage_location = Column(String(1024), nullable=False, default="")
    retention_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    job = relationship("VerificationJob", back_populates="evidence_manifests")


class AssignmentRule(Base):
    __tablename__ = "assignment_rules"

    id = Column(String(80), primary_key=True, default=_gen_id)
    product = Column(String(255), default="")
    feature = Column(String(255), default="", index=True)
    service = Column(String(255), default="")
    owner_team = Column(String(255), nullable=False)
    severity_threshold = Column(String(30), default="Low")
    escalation_window_minutes = Column(Integer, default=0)
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_now_utc, nullable=False)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)


class AssuranceScenario(Base):
    """An approved, bounded V3 verification recipe; never arbitrary agent code."""

    __tablename__ = "assurance_scenarios"

    id = Column(String(80), primary_key=True, default=_gen_id)
    name = Column(String(255), nullable=False)
    feature = Column(String(255), nullable=False, default="", index=True)
    environment = Column(String(80), nullable=False, default="staging", index=True)
    authorized_role = Column(String(120), nullable=False, default="")
    target_origin = Column(String(1024), nullable=False)
    scenario_type = Column(String(40), nullable=False)  # api_contract | data_assertion
    reference_id = Column(String(80), nullable=False, unique=True, index=True)
    max_actions = Column(Integer, nullable=False, default=3)
    is_approved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    jobs = relationship("VerificationJob", back_populates="scenario")


class MonitoringSchedule(Base):
    """Saved continuous-check definition. A scheduler is intentionally out of scope."""

    __tablename__ = "monitoring_schedules"

    id = Column(String(80), primary_key=True, default=_gen_id)
    name = Column(String(255), nullable=False)
    scenario_id = Column(String(80), ForeignKey("assurance_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    target = Column(String(1024), nullable=False)
    environment = Column(String(80), nullable=False, default="staging")
    grouping_key = Column(String(255), nullable=False, index=True)
    interval_minutes = Column(Integer, nullable=False, default=60)
    is_enabled = Column(Boolean, nullable=False, default=True)
    last_triggered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)


# ---------------------------------------------------------------------------
# 6. V3 API contract assurance (feature-flagged, additive)
# ---------------------------------------------------------------------------

class ApiContract(Base):
    """An approved, sanitized API contract registration.

    The original OpenAPI/Postman document is deliberately not retained: it can
    contain authorization examples or test data.  Only normalized, reviewed
    operations and safe assertion rules are persisted.
    """

    __tablename__ = "api_contracts"

    id = Column(String(80), primary_key=True, default=_gen_id)
    name = Column(String(255), nullable=False)
    version = Column(String(100), nullable=False, default="unversioned")
    source_type = Column(String(30), nullable=False)  # openapi | postman
    base_url = Column(String(1024), nullable=False)
    environment = Column(String(40), nullable=False, default="staging")
    is_approved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    operations = relationship("ApiOperation", back_populates="contract", cascade="all, delete-orphan")


class ApiOperation(Base):
    """A normalized approved operation; never stores authorization values."""

    __tablename__ = "api_operations"

    id = Column(String(80), primary_key=True, default=_gen_id)
    contract_id = Column(String(80), ForeignKey("api_contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_key = Column(String(255), nullable=False)
    method = Column(String(10), nullable=False)
    path = Column(String(1024), nullable=False)
    expected_status_codes = Column(JSON, default=list, nullable=False)
    required_fields = Column(JSON, default=list, nullable=False)
    response_schema = Column(JSON, default=dict, nullable=False)
    approved_parameters = Column(JSON, default=list, nullable=False)
    max_latency_ms = Column(Integer, nullable=False, default=3000)
    auth_requirement = Column(String(30), nullable=False, default="none")
    auth_fixture_ref = Column(String(160), nullable=False, default="")
    approved_role = Column(String(120), nullable=False, default="")
    allow_unauthenticated_probe = Column(Boolean, nullable=False, default=False)
    safe_read_only = Column(Boolean, nullable=False, default=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_now_utc, nullable=False)

    contract = relationship("ApiContract", back_populates="operations")
    observations = relationship("ApiObservation", back_populates="operation", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("contract_id", "operation_key", name="uq_api_operation_contract_key"),)


class ApiObservation(Base):
    """Redacted API-validation result linked to V2/V3 traceability IDs."""

    __tablename__ = "api_observations"

    id = Column(String(80), primary_key=True, default=_gen_id)
    operation_id = Column(String(80), ForeignKey("api_operations.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(String(80), ForeignKey("verification_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    evidence_manifest_id = Column(String(80), ForeignKey("evidence_manifests.id", ondelete="SET NULL"), nullable=True, index=True)
    scenario_version = Column(String(100), nullable=False, default="unassigned")
    result = Column(String(30), nullable=False, index=True)  # pass | fail | blocked
    classification = Column(String(100), nullable=False, default="")
    status_code = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    request_metadata = Column(JSON, default=dict, nullable=False)
    assertion_result = Column(JSON, default=dict, nullable=False)
    evidence_link = Column(String(1024), nullable=False, default="")
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)

    operation = relationship("ApiOperation", back_populates="observations")


class DatabaseQueryDefinition(Base):
    """An approved, read-only database query definition for V3 comparisons.

    The query is executed only against the application-configured test/staging
    database.  Production and arbitrary connection details are intentionally
    not represented by this model.
    """

    __tablename__ = "database_query_definitions"

    id = Column(String(80), primary_key=True, default=_gen_id)
    query_id = Column(String(80), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    sql = Column(Text, nullable=False)
    parameter_names = Column(JSON, default=list, nullable=False)
    database_ref = Column(String(80), nullable=False, default="configured-test")
    environment = Column(String(20), nullable=False, default="test")
    is_approved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)


# ---------------------------------------------------------------------------
# 7. V3 dashboard data assurance (feature-flagged, additive)
# ---------------------------------------------------------------------------

class DataAssertionScenario(Base):
    """Approved UI-versus-read-only-source comparison definition."""

    __tablename__ = "data_assertion_scenarios"

    id = Column(String(80), primary_key=True, default=_gen_id)
    name = Column(String(255), nullable=False)
    route = Column(String(1024), nullable=False)
    ui_selector = Column(String(1024), nullable=False)
    metric_label = Column(String(255), nullable=False, default="")
    source_operation_id = Column(String(80), ForeignKey("api_operations.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_field = Column(String(255), nullable=False)
    source_timestamp_field = Column(String(255), nullable=False, default="")
    expected_filters = Column(JSON, default=dict, nullable=False)
    timezone = Column(String(100), nullable=False, default="UTC")
    freshness_window_minutes = Column(Integer, nullable=False, default=60)
    tolerance = Column(String(80), nullable=False, default="0")
    value_format = Column(String(30), nullable=False, default="number")
    is_approved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)
    updated_at = Column(DateTime, default=_now_utc, onupdate=_now_utc, nullable=False)

    observations = relationship("DataAssertionObservation", back_populates="scenario", cascade="all, delete-orphan")


class DataAssertionObservation(Base):
    """A redacted comparison with V2/V3 evidence links and clear classification."""

    __tablename__ = "data_assertion_observations"

    id = Column(String(80), primary_key=True, default=_gen_id)
    scenario_id = Column(String(80), ForeignKey("data_assertion_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(String(80), ForeignKey("verification_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    run_id = Column(String(80), ForeignKey("scan_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    evidence_manifest_id = Column(String(80), ForeignKey("evidence_manifests.id", ondelete="SET NULL"), nullable=True, index=True)
    scenario_version = Column(String(100), nullable=False, default="unassigned")
    result = Column(String(30), nullable=False, index=True)  # pass | fail | blocked
    classification = Column(String(100), nullable=False, default="")
    ui_raw_value = Column(String(1024), nullable=False, default="")
    source_raw_value = Column(String(1024), nullable=False, default="")
    normalized_ui_value = Column(String(80), nullable=True)
    normalized_source_value = Column(String(80), nullable=True)
    delta = Column(String(80), nullable=True)
    filter_context = Column(JSON, default=dict, nullable=False)
    source_filter_context = Column(JSON, default=dict, nullable=False)
    source_fresh_at = Column(DateTime, nullable=True)
    captured_at = Column(DateTime, nullable=False, default=_now_utc)
    source_metadata = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_now_utc, nullable=False, index=True)

    scenario = relationship("DataAssertionScenario", back_populates="observations")

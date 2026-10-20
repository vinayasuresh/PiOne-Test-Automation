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

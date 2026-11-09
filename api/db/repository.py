"""api/db/repository.py: Database repository layer for PiOne Testmate.

Provides centralized, thread-safe database operations for:
- Scan runs, modules, routes, elements, and checkpoints
- Test plans, test cases, and regression suites
- Executions, steps, artifacts, and baseline diffs
- Credential sanitization, audit logging, and optimistic concurrency
"""

from __future__ import annotations

from datetime import datetime, timezone
import hmac
from typing import Any
from uuid import uuid4
from pathlib import Path

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

from api.db.models import (
    ApiContract,
    ApiObservation,
    ApiOperation,
    AssuranceScenario,
    AssignmentRule,
    ApplicationTarget,
    Artifact,
    AuditEvent,
    Checkpoint,
    DataAssertionObservation,
    DataAssertionScenario,
    DatabaseQueryDefinition,
    Execution,
    ExecutionStep,
    EvidenceManifest,
    ApiTestPlanState,
    RegressionSuite,
    RegressionSuiteCase,
    RouteEdge,
    ScanElement,
    ScanModule,
    ScanRoute,
    ScanRun,
    SupportIncident,
    TestCaseModel,
    TestPlan,
    VerificationAttempt,
    VerificationJob,
    MonitoringSchedule,
    InteractiveBrowserSession,
    InteractiveBrowserEvidence,
)
from api.db.session import execute_with_retry, get_db_session
from automation_framework.config.settings import LOG_PATH, REPORT_PATH
from automation_framework.utils.logger import logger
from automation_framework.utils.redactor import redact_sensitive_data


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class DBRepository:
    """Encapsulates all database persistence operations."""

    # -----------------------------------------------------------------------
    # 1. Run Management
    # -----------------------------------------------------------------------

    @staticmethod
    def create_or_update_run(
        run_id: str,
        base_url: str,
        display_name: str | None = None,
        allowed_path_namespace: str = "/",
        status: str = "initialized",
        auth_status: str = "none",
        username: str | None = None,
        selected_modules: list[str] | None = None,
        discovered_modules: list[dict[str, Any]] | None = None,
        execution_summary: dict[str, Any] | None = None,
        progress_pct: float = 0.0,
        error_message: str | None = None,
    ) -> ScanRun:
        def _op(session: Session) -> ScanRun:
            run = session.query(ScanRun).filter(ScanRun.id == run_id).first()
            if not run:
                run = ScanRun(
                    id=run_id,
                    display_name=display_name,
                    base_url=base_url,
                    allowed_path_namespace=allowed_path_namespace,
                    status=status,
                    auth_status=auth_status,
                    username=username,
                    selected_modules=selected_modules or [],
                    discovered_modules=discovered_modules or [],
                    execution_summary=execution_summary or {},
                    progress_pct=progress_pct,
                    error_message=error_message,
                )
                session.add(run)
                # Audit event
                session.add(AuditEvent(run_id=run_id, event_type="run_created", details={"url": base_url}))
            else:
                run.status = status
                run.auth_status = auth_status
                if display_name is not None:
                    run.display_name = display_name
                if username:
                    run.username = username
                if selected_modules is not None:
                    run.selected_modules = selected_modules
                if discovered_modules is not None:
                    run.discovered_modules = discovered_modules
                if execution_summary is not None:
                    run.execution_summary = execution_summary
                if progress_pct is not None:
                    run.progress_pct = progress_pct
                if error_message is not None:
                    run.error_message = error_message
                run.updated_at = _now_utc()
                session.add(AuditEvent(run_id=run_id, event_type="run_updated", details={"status": status}))

            session.flush()
            return run

        return execute_with_retry(_op)

    @staticmethod
    def list_runs(
        base_url: str | None = None,
        status: str | None = None,
        is_archived: bool = False,
        skip: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            query = session.query(ScanRun).filter(ScanRun.is_archived == is_archived)
            if base_url:
                query = query.filter(ScanRun.base_url.ilike(f"%{base_url.strip()}%"))
            if status:
                query = query.filter(ScanRun.status == status)

            runs = query.order_by(desc(ScanRun.created_at)).offset(skip).limit(limit).all()

            results = []
            for r in runs:
                routes_count = session.query(func.count(ScanRoute.id)).filter(ScanRoute.run_id == r.id).scalar() or 0
                completed_modules = [m for m in (r.selected_modules or []) if m in [mod.module_id for mod in r.modules if mod.status == "completed"]]
                pending_modules = [m for m in (r.selected_modules or []) if m not in completed_modules]

                results.append({
                    "run_id": r.id,
                    "display_name": r.display_name,
                    "application_url": r.base_url,
                    "allowed_scope": r.allowed_path_namespace,
                    "status": r.status,
                    "auth_status": r.auth_status,
                    "progress_percentage": r.progress_pct,
                    "selected_modules": r.selected_modules or [],
                    "completed_modules": completed_modules,
                    "pending_modules": pending_modules,
                    "route_count": routes_count,
                    "execution_summary": r.execution_summary or {},
                    "is_archived": r.is_archived,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                })
            return results

        return execute_with_retry(_op)

    @staticmethod
    def _run_to_dict(session: Session, run: ScanRun) -> dict[str, Any]:
        modules = [
            {
                "module_id": m.module_id,
                "name": m.name,
                "route": m.route,
                "status": m.status,
                "routes_count": m.routes_count,
            }
            for m in (run.modules or [])
        ]
        routes = [
            {
                "path": rt.path,
                "menu_name": rt.menu_name,
                "status": rt.status,
                "score": rt.score,
                "dom_stable": rt.dom_stable,
                "page_load_success": rt.page_load_success,
                "target_count": rt.target_count,
            }
            for rt in (run.routes or [])
        ]
        latest_chk = session.query(Checkpoint).filter(Checkpoint.run_id == run.id).order_by(desc(Checkpoint.version)).first()

        return {
            "run_id": run.id,
            "display_name": run.display_name,
            "application_url": run.base_url,
            "allowed_scope": run.allowed_path_namespace,
            "status": run.status,
            "auth_status": run.auth_status,
            "username": run.username,
            "progress_percentage": run.progress_pct,
            "selected_modules": run.selected_modules or [],
            "discovered_modules": run.discovered_modules or [],
            "modules": modules,
            "routes": routes,
            "route_count": len(routes),
            "execution_summary": run.execution_summary or {},
            "is_archived": run.is_archived,
            "latest_checkpoint_version": latest_chk.version if latest_chk else None,
            "latest_checkpoint_time": latest_chk.updated_at.isoformat() if latest_chk and latest_chk.updated_at else None,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        }

    @staticmethod
    def get_run(run_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            run = session.query(ScanRun).filter(ScanRun.id == run_id).first()
            if not run:
                return None
            return DBRepository._run_to_dict(session, run)

        return execute_with_retry(_op)

    @staticmethod
    def archive_run(run_id: str) -> bool:
        def _op(session: Session) -> bool:
            run = session.query(ScanRun).filter(ScanRun.id == run_id).first()
            if not run:
                return False
            run.is_archived = True
            run.updated_at = _now_utc()
            session.add(AuditEvent(run_id=run_id, event_type="run_archived"))
            return True

        return execute_with_retry(_op)

    @staticmethod
    def delete_run(run_id: str) -> bool:
        def _op(session: Session) -> bool:
            run = session.query(ScanRun).filter(ScanRun.id == run_id).first()
            if not run:
                return False
            session.delete(run)
            return True

        return execute_with_retry(_op)

    @staticmethod
    def duplicate_run(source_run_id: str, new_run_id: str | None = None) -> dict[str, Any] | None:
        target_run_id = new_run_id or uuid4().hex

        def _op(session: Session) -> dict[str, Any] | None:
            source = session.query(ScanRun).filter(ScanRun.id == source_run_id).first()
            if not source:
                return None

            dup = ScanRun(
                id=target_run_id,
                display_name=source.display_name,
                base_url=source.base_url,
                allowed_path_namespace=source.allowed_path_namespace,
                status="initialized",
                auth_status=source.auth_status,
                username=source.username,
                selected_modules=source.selected_modules,
                discovered_modules=source.discovered_modules,
                execution_summary={},
                progress_pct=0.0,
            )
            session.add(dup)
            session.add(AuditEvent(run_id=target_run_id, event_type="run_duplicated", details={"source_run_id": source_run_id}))
            session.flush()

            return DBRepository._run_to_dict(session, dup)

        return execute_with_retry(_op)

    # -----------------------------------------------------------------------
    # 2. Checkpoints (Atomic & Optimistic Concurrency)
    # -----------------------------------------------------------------------

    @staticmethod
    def save_checkpoint(
        run_id: str,
        checkpoint_data: dict[str, Any],
        expected_version: int | None = None,
    ) -> Checkpoint:
        safe_data = redact_sensitive_data(checkpoint_data)

        def _op(session: Session) -> Checkpoint:
            # Optimistic lock check
            latest = session.query(Checkpoint).filter(Checkpoint.run_id == run_id).order_by(desc(Checkpoint.version)).first()
            current_ver = latest.version if latest else 0

            if expected_version is not None and expected_version != current_ver:
                raise ValueError(
                    f"Optimistic lock conflict: checkpoint version {expected_version} does not match current {current_ver}"
                )

            new_ver = current_ver + 1
            chk = Checkpoint(
                run_id=run_id,
                version=new_ver,
                namespace=safe_data.get("namespace", ""),
                current_module=safe_data.get("current_module_id", ""),
                current_route=safe_data.get("current_route", ""),
                visited_routes=safe_data.get("visited_routes", []),
                pending_routes=safe_data.get("pending_routes", []),
                completed_routes=safe_data.get("completed_routes", []),
                failed_routes=safe_data.get("failed_routes", []),
                partial_routes=safe_data.get("partial_routes", []),
                scroll_position=safe_data.get("scroll_position", {}),
                state_data=safe_data,
                schema_version=safe_data.get("schema_version", "2.0"),
            )
            session.add(chk)

            # Sync route records into scan_routes
            for r_url, val in safe_data.get("route_validations", {}).items():
                existing_r = session.query(ScanRoute).filter(ScanRoute.run_id == run_id, ScanRoute.path == r_url).first()
                if not existing_r:
                    intel = safe_data.get("page_intelligence", {}).get(r_url, {})
                    existing_r = ScanRoute(
                        run_id=run_id,
                        path=r_url,
                        menu_name=intel.get("menu_name", ""),
                        purpose=intel.get("purpose", ""),
                        status=val.get("status", "WORKING"),
                        score=val.get("score", 100),
                        dom_stable=val.get("dom_stable", True),
                        page_load_success=val.get("page_load_success", True),
                        redirected=val.get("redirected", False),
                        failed_api_responses=val.get("failed_api_responses", 0),
                        target_count=val.get("target_count", 0),
                        diagnostic_signals=val,
                    )
                    session.add(existing_r)
                else:
                    existing_r.status = val.get("status", existing_r.status)
                    existing_r.score = val.get("score", existing_r.score)
                    existing_r.dom_stable = val.get("dom_stable", existing_r.dom_stable)

            # Update run progress
            run = session.query(ScanRun).filter(ScanRun.id == run_id).first()
            if run:
                run.updated_at = _now_utc()
                total_r = len(safe_data.get("visited_routes", [])) + len(safe_data.get("pending_routes", []))
                if total_r > 0:
                    run.progress_pct = round(len(safe_data.get("visited_routes", [])) * 100.0 / total_r, 1)

            session.flush()
            return chk

        return execute_with_retry(_op)

    @staticmethod
    def get_latest_checkpoint(run_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            chk = session.query(Checkpoint).filter(Checkpoint.run_id == run_id).order_by(desc(Checkpoint.version)).first()
            if not chk:
                return None
            return {
                "checkpoint_id": chk.id,
                "run_id": chk.run_id,
                "version": chk.version,
                "namespace": chk.namespace,
                "current_module": chk.current_module,
                "current_route": chk.current_route,
                "visited_routes": chk.visited_routes or [],
                "pending_routes": chk.pending_routes or [],
                "completed_routes": chk.completed_routes or [],
                "failed_routes": chk.failed_routes or [],
                "partial_routes": chk.partial_routes or [],
                "scroll_position": chk.scroll_position or {},
                "state_data": chk.state_data or {},
                "schema_version": chk.schema_version,
                "created_at": chk.created_at.isoformat() if chk.created_at else None,
                "updated_at": chk.updated_at.isoformat() if chk.updated_at else None,
            }

        return execute_with_retry(_op)

    # -----------------------------------------------------------------------
    # 3. Regression Suites & Test Cases
    # -----------------------------------------------------------------------

    @staticmethod
    def save_test_cases(run_id: str, test_cases: list[dict[str, Any]], name: str = "Generated Test Plan") -> dict[str, Any]:
        """Persist the generated plan so History and Regression can reuse it.

        The former API called this method but it did not exist, so plans were
        only kept in the in-memory run context and suites displayed 0/0 cases.
        """
        def _op(session: Session) -> dict[str, Any]:
            # Re-generating a plan updates the current cases instead of
            # creating duplicate testcase IDs that make suites ambiguous.
            plan = (
                session.query(TestPlan)
                .filter(TestPlan.run_id == run_id, TestPlan.is_baseline == False)
                .order_by(desc(TestPlan.created_at))
                .first()
            )
            if not plan:
                plan = TestPlan(run_id=run_id, name=name, version="1.0", is_baseline=False)
                session.add(plan)
                session.flush()
            else:
                plan.name = name

            unique_cases: dict[str, dict[str, Any]] = {}
            for tc in test_cases or []:
                payload = tc if isinstance(tc, dict) else {}
                tc_key = str(payload.get("tc_id") or payload.get("id") or "").strip()
                if tc_key and tc_key not in unique_cases:
                    unique_cases[tc_key] = payload

            existing = {
                case.tc_id: case
                for case in session.query(TestCaseModel).filter(TestCaseModel.plan_id == plan.id).all()
            }
            for tc_id, payload in unique_cases.items():
                report_fields = {key: payload.get(key) for key in (
                    "screen", "feature", "testcase", "steps_to_execute", "test_status",
                    "actual_result", "severity", "bug_status", "notes_reason",
                    "bug_sheet_current_version", "tested_version", "test_type", "source_references",
                ) if key in payload}
                values = dict(
                    run_id=run_id,
                    tc_id=tc_id,
                    title=str(payload.get("title") or payload.get("testcase") or "Generated testcase"),
                    module=str(payload.get("module") or payload.get("screen") or "General"),
                    route=str(payload.get("route") or "/"), tags=payload.get("tags") or [],
                    priority=str(payload.get("priority") or "Medium"), prerequisites=payload.get("prerequisites") or [],
                    locators={"_report": report_fields, **(payload.get("locators") or {})},
                    steps=payload.get("steps") or [], safety_classification=str(payload.get("safety_classification") or "safe_action"),
                )
                case = existing.get(tc_id)
                if case:
                    for key, value in values.items():
                        setattr(case, key, value)
                else:
                    session.add(TestCaseModel(plan_id=plan.id, **values))
            session.flush()
            return {"plan_id": plan.id, "run_id": run_id, "name": name, "case_count": len(unique_cases)}
        return execute_with_retry(_op)

    @staticmethod
    def create_regression_suite(
        name: str,
        base_url: str,
        environment: str = "production",
        description: str = "",
        baseline_run_id: str | None = None,
        test_case_ids: list[str] | None = None,
        filter_tags: list[str] | None = None,
        filter_modules: list[str] | None = None,
    ) -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            suite = RegressionSuite(
                name=name,
                base_url=base_url,
                environment=environment,
                description=description,
                baseline_run_id=baseline_run_id,
                filter_tags=filter_tags or [],
                filter_modules=filter_modules or [],
            )
            session.add(suite)
            session.flush()

            # Link test cases if provided
            if test_case_ids:
                for tc_id in test_case_ids:
                    case = session.query(TestCaseModel).filter(
                        (TestCaseModel.id == tc_id) | (TestCaseModel.tc_id == tc_id)
                    ).first()
                    if case:
                        session.add(RegressionSuiteCase(suite_id=suite.id, test_case_id=case.id, is_enabled=True, tags=case.tags or []))

            session.flush()
            return DBRepository._suite_to_dict(session, suite)

        return execute_with_retry(_op)

    @staticmethod
    def _suite_to_dict(session: Session, suite: RegressionSuite) -> dict[str, Any]:
        cases = []
        suite_cases = session.query(RegressionSuiteCase).filter(RegressionSuiteCase.suite_id == suite.id).all()
        for sc in suite_cases:
            tc = session.query(TestCaseModel).filter(TestCaseModel.id == sc.test_case_id).first()
            if tc:
                cases.append({
                    "id": tc.id,
                    "tc_id": tc.tc_id,
                    "title": tc.title,
                    "module": tc.module,
                    "route": tc.route,
                    "tags": tc.tags or [],
                    "priority": tc.priority,
                    "safety_classification": tc.safety_classification,
                    "latest_result": tc.latest_result,
                    "is_enabled": sc.is_enabled,
                    "steps_count": len(tc.steps or []),
                })

        # Repair/read-through suites created before automatic case linking was
        # added.  Their baseline run still identifies the intended plan, so
        # expose those cases instead of showing a misleading 0 / 0 suite.
        if not cases and suite.baseline_run_id:
            seen_tc_ids: set[str] = set()
            fallback_cases = session.query(TestCaseModel).filter(
                TestCaseModel.run_id == suite.baseline_run_id
            ).order_by(desc(TestCaseModel.created_at)).all()
            for tc in fallback_cases:
                if tc.tc_id in seen_tc_ids:
                    continue
                seen_tc_ids.add(tc.tc_id)
                cases.append({
                    "id": tc.id,
                    "tc_id": tc.tc_id,
                    "title": tc.title,
                    "module": tc.module,
                    "route": tc.route,
                    "tags": tc.tags or [],
                    "priority": tc.priority,
                    "safety_classification": tc.safety_classification,
                    "latest_result": tc.latest_result,
                    "is_enabled": True,
                    "steps_count": len(tc.steps or []),
                })

        return {
            "suite_id": suite.id,
            "name": suite.name,
            "description": suite.description,
            "base_url": suite.base_url,
            "environment": suite.environment,
            "baseline_run_id": suite.baseline_run_id,
            "filter_tags": suite.filter_tags or [],
            "filter_modules": suite.filter_modules or [],
            "cases": cases,
            "total_cases": len(cases),
            "enabled_cases": sum(1 for c in cases if c["is_enabled"]),
            "created_at": suite.created_at.isoformat() if suite.created_at else None,
            "updated_at": suite.updated_at.isoformat() if suite.updated_at else None,
        }

    @staticmethod
    def list_regression_suites(base_url: str | None = None, is_archived: bool = False) -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            query = session.query(RegressionSuite).filter(RegressionSuite.is_archived == is_archived)
            if base_url:
                query = query.filter(RegressionSuite.base_url.ilike(f"%{base_url.strip()}%"))

            suites = query.order_by(desc(RegressionSuite.created_at)).all()
            results = []
            for s in suites:
                total_cases = session.query(func.count(RegressionSuiteCase.id)).filter(RegressionSuiteCase.suite_id == s.id).scalar() or 0
                enabled_cases = session.query(func.count(RegressionSuiteCase.id)).filter(RegressionSuiteCase.suite_id == s.id, RegressionSuiteCase.is_enabled == True).scalar() or 0
                if total_cases == 0 and s.baseline_run_id:
                    # Read through legacy suites whose case links were not
                    # created, while keeping the suite itself unchanged.
                    case_rows = session.query(TestCaseModel.tc_id).filter(
                        TestCaseModel.run_id == s.baseline_run_id
                    ).all()
                    unique_ids = {row[0] for row in case_rows if row[0]}
                    total_cases = enabled_cases = len(unique_ids)
                results.append({
                    "suite_id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "base_url": s.base_url,
                    "environment": s.environment,
                    "baseline_run_id": s.baseline_run_id,
                    "filter_tags": s.filter_tags or [],
                    "filter_modules": s.filter_modules or [],
                    "total_cases": total_cases,
                    "enabled_cases": enabled_cases,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                })
            return results

        return execute_with_retry(_op)

    @staticmethod
    def get_regression_suite(suite_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            suite = session.query(RegressionSuite).filter(RegressionSuite.id == suite_id).first()
            if not suite:
                return None
            return DBRepository._suite_to_dict(session, suite)

        return execute_with_retry(_op)

    @staticmethod
    def update_regression_suite(
        suite_id: str,
        name: str | None = None,
        description: str | None = None,
        environment: str | None = None,
        case_toggles: dict[str, bool] | None = None,  # {test_case_id: bool}
        filter_tags: list[str] | None = None,
        filter_modules: list[str] | None = None,
    ) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            suite = session.query(RegressionSuite).filter(RegressionSuite.id == suite_id).first()
            if not suite:
                return None

            if name is not None:
                suite.name = name
            if description is not None:
                suite.description = description
            if environment is not None:
                suite.environment = environment
            if filter_tags is not None:
                suite.filter_tags = filter_tags
            if filter_modules is not None:
                suite.filter_modules = filter_modules

            if case_toggles:
                for sc in suite.suite_cases:
                    if sc.test_case_id in case_toggles:
                        sc.is_enabled = case_toggles[sc.test_case_id]

            suite.updated_at = _now_utc()
            session.flush()
            return DBRepository._suite_to_dict(session, suite)

        return execute_with_retry(_op)

    @staticmethod
    def duplicate_regression_suite(suite_id: str, new_name: str | None = None) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            suite = session.query(RegressionSuite).filter(RegressionSuite.id == suite_id).first()
            if not suite:
                return None

            dup = RegressionSuite(
                name=new_name or f"{suite.name} (Copy)",
                description=suite.description,
                base_url=suite.base_url,
                environment=suite.environment,
                baseline_run_id=suite.baseline_run_id,
                filter_tags=suite.filter_tags,
                filter_modules=suite.filter_modules,
            )
            session.add(dup)
            session.flush()

            for sc in suite.suite_cases:
                session.add(RegressionSuiteCase(suite_id=dup.id, test_case_id=sc.test_case_id, is_enabled=sc.is_enabled, tags=sc.tags))

            session.flush()
            return DBRepository._suite_to_dict(session, dup)

        return execute_with_retry(_op)

    @staticmethod
    def archive_regression_suite(suite_id: str) -> bool:
        def _op(session: Session) -> bool:
            suite = session.query(RegressionSuite).filter(RegressionSuite.id == suite_id).first()
            if not suite:
                return False
            suite.is_archived = True
            suite.updated_at = _now_utc()
            return True

        return execute_with_retry(_op)

    @staticmethod
    def delete_regression_suite(suite_id: str) -> bool:
        def _op(session: Session) -> bool:
            suite = session.query(RegressionSuite).filter(RegressionSuite.id == suite_id).first()
            if not suite:
                return False
            session.delete(suite)
            return True

        return execute_with_retry(_op)

    @staticmethod
    def delete_all_regression_suites() -> int:
        """Delete every regression suite and return the number removed."""
        def _op(session: Session) -> int:
            suites = session.query(RegressionSuite).all()
            count = len(suites)
            for suite in suites:
                session.delete(suite)
            return count

        return execute_with_retry(_op)

    @staticmethod
    def delete_all_artifact_records() -> int:
        """Delete artifact metadata records; files are removed by the API layer."""
        def _op(session: Session) -> int:
            records = session.query(Artifact).all()
            count = len(records)
            for record in records:
                session.delete(record)
            return count

        return execute_with_retry(_op)

    @staticmethod
    def register_artifacts(
        run_id: str,
        artifacts: dict[str, Any],
        execution_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Persist metadata for generated files without copying file contents into PostgreSQL.

        Only existing files beneath the configured report/log roots are accepted. The
        database stores a portable relative storage key, run/execution lineage, and
        size; the files themselves remain in the artifact filesystem.
        """
        if not run_id or not isinstance(artifacts, dict):
            return []

        root_map = {
            REPORT_PATH.resolve(): "reports",
            LOG_PATH.resolve(): "logs",
        }
        prepared: list[tuple[str, str, int]] = []
        for artifact_type, raw_path in artifacts.items():
            if not isinstance(raw_path, (str, Path)):
                continue
            try:
                path = Path(raw_path).resolve(strict=True)
                if not path.is_file():
                    continue
                storage_key = None
                for root, prefix in root_map.items():
                    if path.is_relative_to(root):
                        storage_key = f"{prefix}/{path.relative_to(root).as_posix()}"
                        break
                if storage_key:
                    safe_type = "".join(ch for ch in str(artifact_type).lower() if ch.isalnum() or ch in "_- ")[:50].strip()
                    if safe_type:
                        prepared.append((safe_type, storage_key, path.stat().st_size))
            except (OSError, RuntimeError, ValueError):
                continue

        if not prepared:
            return []

        def _op(session: Session) -> list[dict[str, Any]]:
            if not session.query(ScanRun.id).filter(ScanRun.id == run_id).first():
                logger.warning("Skipping artifact metadata for run without a persisted scan record: %s", run_id)
                return []
            registered: list[dict[str, Any]] = []
            for artifact_type, storage_key, file_size in prepared:
                record = (
                    session.query(Artifact)
                    .filter(
                        Artifact.run_id == run_id,
                        Artifact.artifact_type == artifact_type,
                        Artifact.file_path == storage_key,
                    )
                    .first()
                )
                created = record is None
                if record is None:
                    record = Artifact(
                        id=f"artifact_{uuid4().hex}",
                        run_id=run_id,
                        execution_id=execution_id,
                        artifact_type=artifact_type,
                        file_path=storage_key,
                        file_size=file_size,
                    )
                    session.add(record)
                else:
                    record.execution_id = execution_id or record.execution_id
                    record.file_size = file_size
                if created:
                    session.add(AuditEvent(
                        run_id=run_id,
                        event_type="artifact_created",
                        details={"artifact_id": record.id, "artifact_type": artifact_type, "file_size": file_size},
                    ))
                registered.append({
                    "id": record.id,
                    "run_id": run_id,
                    "execution_id": execution_id or record.execution_id,
                    "artifact_type": artifact_type,
                    "file_path": storage_key,
                    "file_size": file_size,
                })
            return registered

        return execute_with_retry(_op)

    @staticmethod
    def record_artifact_download(
        run_id: str,
        artifact_id: str | None,
        artifact_type: str,
        filename: str,
        file_size: int,
    ) -> bool:
        """Audit a successful artifact GET using only safe display metadata."""
        safe_filename = redact_sensitive_data({"filename": Path(filename).name[:255]}).get("filename", "artifact")
        safe_type = "".join(ch for ch in str(artifact_type).lower() if ch.isalnum() or ch in "_- ")[:50].strip()

        def _op(session: Session) -> bool:
            if not session.query(ScanRun.id).filter(ScanRun.id == run_id).first():
                return False
            session.add(AuditEvent(
                run_id=run_id,
                event_type="artifact_downloaded",
                details={
                    "artifact_id": artifact_id,
                    "artifact_type": safe_type,
                    "filename": safe_filename,
                    "file_size": max(0, int(file_size)),
                },
            ))
            return True

        return execute_with_retry(_op)

    @staticmethod
    def list_artifacts(run_id: str | None = None) -> list[dict[str, Any]]:
        """List artifact metadata from the configured database."""
        def _op(session: Session) -> list[dict[str, Any]]:
            query = session.query(Artifact)
            if run_id:
                query = query.filter(Artifact.run_id == run_id)
            rows = query.order_by(desc(Artifact.created_at)).all()
            return [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "execution_id": row.execution_id,
                    "artifact_type": row.artifact_type,
                    "file_path": row.file_path,
                    "file_size": row.file_size or 0,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]

        return execute_with_retry(_op)

    # -----------------------------------------------------------------------
    # 4. Executions & Steps
    # -----------------------------------------------------------------------

    @staticmethod
    def record_execution(
        execution_id: str,
        run_id: str,
        suite_id: str | None = None,
        baseline_id: str | None = None,
        status: str = "completed",
        rows: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
        diff: dict[str, Any] | None = None,
    ) -> Execution:
        def _op(session: Session) -> Execution:
            safe_rows = redact_sensitive_data(rows or [])
            total = len(safe_rows)
            passed = sum(1 for r in safe_rows if r.get("Status") == "Pass")
            failed = sum(1 for r in safe_rows if r.get("Status") == "Fail")
            skipped = sum(1 for r in safe_rows if r.get("Status") == "Skipped")

            exec_record = Execution(
                id=execution_id,
                run_id=run_id,
                suite_id=suite_id,
                baseline_id=baseline_id,
                status=status,
                total_steps=total,
                passed_steps=passed,
                failed_steps=failed,
                skipped_steps=skipped,
                diff_summary=diff or {},
            )
            session.add(exec_record)

            for r in safe_rows:
                step_record = ExecutionStep(
                    execution_id=execution_id,
                    tc_id=r.get("TC_ID", ""),
                    step_id=r.get("Step_ID", ""),
                    action=r.get("Action", ""),
                    element_identity=r.get("element_identity", ""),
                    locator_used=r.get("locator_used", ""),
                    status=r.get("Status", "Skipped"),
                    actual_result=r.get("Actual_Result", ""),
                    failure_info=r.get("Failure_Info", ""),
                    failure_category=r.get("failure_category", ""),
                    console_errors=r.get("console_errors", []),
                    network_errors=r.get("network_errors", []),
                    artifact_path=r.get("Artifact_Path", ""),
                )
                session.add(step_record)

                # Update latest result on TestCaseModel
                tc = session.query(TestCaseModel).filter(TestCaseModel.run_id == run_id, TestCaseModel.tc_id == r.get("TC_ID")).first()
                if tc:
                    tc.latest_result = r.get("Status", tc.latest_result)
                    tc.last_execution_time = _now_utc()

            session.flush()
            return exec_record

        return execute_with_retry(_op)

    # -----------------------------------------------------------------------
    # 5. Baselines & Comparisons
    # -----------------------------------------------------------------------

    @staticmethod
    def save_baseline(
        run_id: str,
        name: str = "Baseline Plan",
        version: str = "1.0",
        overwrite: bool = False,
        test_cases: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            existing = session.query(TestPlan).filter(TestPlan.run_id == run_id, TestPlan.is_baseline == True).first()
            if existing and not overwrite:
                raise ValueError("Baseline already exists for this run. Explicit overwrite confirmation is required.")

            if existing:
                plan = existing
                plan.name = name
                plan.version = version
            else:
                plan = TestPlan(run_id=run_id, name=name, version=version, is_baseline=True)
                session.add(plan)
            session.flush()

            if test_cases:
                # Remove prior cases linked to this plan
                session.query(TestCaseModel).filter(TestCaseModel.plan_id == plan.id).delete()
                for tc in test_cases:
                    case_model = TestCaseModel(
                        plan_id=plan.id,
                        run_id=run_id,
                        tc_id=tc.get("tc_id", ""),
                        title=tc.get("title", ""),
                        module=tc.get("module", "General"),
                        route=tc.get("route", "/"),
                        tags=tc.get("tags") or ["regression"],
                        priority=tc.get("priority", "Medium"),
                        prerequisites=tc.get("prerequisites", []),
                        locators=tc.get("locators", {}),
                        steps=tc.get("steps", []),
                        safety_classification=tc.get("safety_classification", "safe_action"),
                        baseline_version=version,
                    )
                    session.add(case_model)

            session.flush()
            return {"plan_id": plan.id, "run_id": run_id, "name": plan.name, "version": plan.version}

        return execute_with_retry(_op)

    @staticmethod
    def list_baselines(base_url: str | None = None) -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            query = session.query(TestPlan).filter(TestPlan.is_baseline == True)
            if base_url:
                query = query.join(ScanRun, TestPlan.run_id == ScanRun.id).filter(ScanRun.base_url.ilike(f"%{base_url.strip()}%"))
            plans = query.order_by(desc(TestPlan.created_at)).all()
            return [
                {
                    "plan_id": p.id,
                    "run_id": p.run_id,
                    "name": p.name,
                    "version": p.version,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
                for p in plans
            ]

        return execute_with_retry(_op)

    @staticmethod
    def compare_baseline(current_run_id: str, baseline_run_id: str) -> dict[str, Any]:
        """Compare current scan/execution against a baseline run."""
        def _op(session: Session) -> dict[str, Any]:
            base_plan = (
                session.query(TestPlan).filter(TestPlan.run_id == baseline_run_id, TestPlan.is_baseline == True).first()
                or session.query(TestPlan).filter(TestPlan.id == baseline_run_id).first()
            )
            actual_baseline_run_id = base_plan.run_id if base_plan else baseline_run_id
            base_cases = session.query(TestCaseModel).filter(TestCaseModel.run_id == actual_baseline_run_id).all()
            curr_cases = session.query(TestCaseModel).filter(TestCaseModel.run_id == current_run_id).all()

            base_by_tcid = {c.tc_id: c for c in base_cases}
            curr_by_tcid = {c.tc_id: c for c in curr_cases}

            newly_passing = []
            newly_failing = []
            changed_locators = []
            changed_expected = []

            for tcid, curr in curr_by_tcid.items():
                base = base_by_tcid.get(tcid)
                if base:
                    if base.latest_result != "Pass" and curr.latest_result == "Pass":
                        newly_passing.append(tcid)
                    elif base.latest_result != "Fail" and curr.latest_result == "Fail":
                        newly_failing.append(tcid)
                    if base.locators != curr.locators:
                        changed_locators.append({"tc_id": tcid, "baseline": base.locators, "current": curr.locators})
                    if base.title != curr.title or base.steps != curr.steps:
                        changed_expected.append({"tc_id": tcid, "title": curr.title})

            # Check removed routes / elements
            base_routes = {r.path for r in session.query(ScanRoute).filter(ScanRoute.run_id == baseline_run_id).all()}
            curr_routes = {r.path for r in session.query(ScanRoute).filter(ScanRoute.run_id == current_run_id).all()}
            removed_routes = sorted(base_routes - curr_routes)

            base_elements = {e.element_identity for e in session.query(ScanElement).filter(ScanElement.run_id == baseline_run_id).all()}
            curr_elements = {e.element_identity for e in session.query(ScanElement).filter(ScanElement.run_id == current_run_id).all()}
            removed_elements = sorted(base_elements - curr_elements)

            added_cases = sorted(set(curr_by_tcid) - set(base_by_tcid))
            removed_cases = sorted(set(base_by_tcid) - set(curr_by_tcid))
            return {
                "current_run_id": current_run_id,
                "baseline_run_id": actual_baseline_run_id,
                "baseline_plan_id": base_plan.id if base_plan else None,
                "newly_passing": newly_passing,
                "newly_failing": newly_failing,
                "removed_routes": removed_routes,
                "removed_elements": removed_elements,
                "changed_locators": changed_locators,
                "changed_expected": changed_expected,
                "added_cases": added_cases,
                "removed_cases": removed_cases,
                "differences": {
                    "added_cases": added_cases,
                    "removed_cases": removed_cases,
                    "changed_locators": changed_locators,
                    "changed_expected": changed_expected,
                    "newly_passing": newly_passing,
                    "newly_failing": newly_failing,
                },
                "unchanged_count": len(set(base_by_tcid) & set(curr_by_tcid)) - len(newly_failing) - len(newly_passing),
            }

        return execute_with_retry(_op)

    # -----------------------------------------------------------------------
    # 6. V3 assurance records (additive; no V2 lifecycle ownership)
    # -----------------------------------------------------------------------

    _V3_JOB_TRANSITIONS = {
        "created": {"queued", "cancelled"},
        "queued": {"running", "blocked", "cancelled"},
        "running": {"completed", "failed", "blocked", "inconclusive"},
        "blocked": {"queued", "cancelled"},
        "failed": {"queued", "cancelled"},
        "inconclusive": {"queued", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }
    @staticmethod
    def _v3_attempt_to_dict(attempt: VerificationAttempt) -> dict[str, Any]:
        return {
            "attempt_id": attempt.id,
            "attempt_number": attempt.attempt_number,
            "worker_id": attempt.worker_id,
            "state": attempt.state,
            "terminal_outcome": attempt.terminal_outcome,
            "failure_category": attempt.failure_category,
            "event_sequence": attempt.event_sequence or [],
            "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
            "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
        }

    @staticmethod
    def _v3_manifest_to_dict(manifest: EvidenceManifest) -> dict[str, Any]:
        return {
            "manifest_id": manifest.id,
            "artifact_id": manifest.artifact_id,
            "run_id": manifest.run_id,
            "source_step": manifest.source_step,
            "source_route": manifest.source_route,
            "checksum": manifest.checksum,
            "content_type": manifest.content_type,
            "storage_location": manifest.storage_location,
            "retention_expires_at": manifest.retention_expires_at.isoformat() if manifest.retention_expires_at else None,
            "created_at": manifest.created_at.isoformat() if manifest.created_at else None,
        }

    @staticmethod
    def _v3_job_to_dict(session: Session, job: VerificationJob) -> dict[str, Any]:
        attempts = (
            session.query(VerificationAttempt)
            .filter(VerificationAttempt.job_id == job.id)
            .order_by(VerificationAttempt.attempt_number)
            .all()
        )
        manifests = (
            session.query(EvidenceManifest)
            .filter(EvidenceManifest.job_id == job.id)
            .order_by(EvidenceManifest.created_at)
            .all()
        )
        return {
            "job_id": job.id,
            "incident_id": job.incident_id,
            "run_id": job.run_id,
            "execution_id": job.execution_id,
            "checkpoint_id": job.checkpoint_id,
            "artifact_id": job.artifact_id,
            "scenario_id": job.scenario_id,
            "scenario_version": job.scenario_version,
            "target": job.target,
            "authorized_role": job.authorized_role,
            "mode": job.mode,
            "state": job.state,
            "verdict": job.verdict,
            "technical_category": job.technical_category,
            "grouping_key": job.grouping_key,
            "retry_limit": job.retry_limit,
            "policy_decision": job.policy_decision or {},
            "attempts": [DBRepository._v3_attempt_to_dict(item) for item in attempts],
            "evidence_manifests": [DBRepository._v3_manifest_to_dict(item) for item in manifests],
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }

    @staticmethod
    def _v3_incident_to_dict(session: Session, incident: SupportIncident) -> dict[str, Any]:
        jobs = session.query(VerificationJob).filter(VerificationJob.incident_id == incident.id).order_by(VerificationJob.created_at).all()
        return {
            "incident_id": incident.id,
            "correlation_id": incident.correlation_id,
            "ticket_reference": incident.ticket_reference,
            "grouping_key": incident.grouping_key,
            "summary": incident.summary,
            "affected_feature": incident.affected_feature,
            "reporter_role": incident.reporter_role,
            "expected_behavior": incident.expected_behavior,
            "actual_behavior": incident.actual_behavior,
            "priority": incident.priority,
            "status": incident.status,
            "owner_team": incident.owner_team,
            "next_action": incident.next_action,
            "last_outcome": incident.last_outcome,
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "jobs": [DBRepository._v3_job_to_dict(session, item) for item in jobs],
            "created_at": incident.created_at.isoformat() if incident.created_at else None,
            "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
        }

    @staticmethod
    def create_or_get_v3_incident_job(payload: dict[str, Any]) -> dict[str, Any]:
        """Create exactly one sanitized incident/job pair per correlation ID."""
        safe = redact_sensitive_data(payload)

        def _op(session: Session) -> dict[str, Any]:
            existing = session.query(SupportIncident).filter(SupportIncident.correlation_id == safe["correlation_id"]).first()
            if existing:
                return {"created": False, "incident": DBRepository._v3_incident_to_dict(session, existing)}

            links = safe.get("links") or {}
            incident = SupportIncident(
                correlation_id=safe["correlation_id"],
                ticket_reference=safe.get("ticket_reference"),
                grouping_key=safe["grouping_key"],
                summary=safe["summary"],
                affected_feature=safe.get("affected_feature", ""),
                reporter_role=safe.get("reporter_role", ""),
                expected_behavior=safe.get("expected_behavior", ""),
                actual_behavior=safe.get("actual_behavior", ""),
                priority=safe.get("priority", "Medium"),
            )
            session.add(incident)
            session.flush()
            job = VerificationJob(
                incident_id=incident.id,
                run_id=links.get("run_id"),
                execution_id=links.get("execution_id"),
                checkpoint_id=links.get("checkpoint_id"),
                artifact_id=links.get("artifact_id"),
                scenario_version=safe.get("scenario_version", "unassigned"),
                target=safe.get("target", ""),
                authorized_role=safe.get("authorized_role", ""),
                mode=safe.get("mode", "functional_only"),
                grouping_key=safe["grouping_key"],
                retry_limit=safe.get("retry_limit", 0),
            )
            session.add(job)
            session.flush()
            return {"created": True, "incident": DBRepository._v3_incident_to_dict(session, incident)}

        return execute_with_retry(_op)

    @staticmethod
    def get_v3_incident(incident_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            incident = session.query(SupportIncident).filter(SupportIncident.id == incident_id).first()
            return DBRepository._v3_incident_to_dict(session, incident) if incident else None
        return execute_with_retry(_op)

    @staticmethod
    def list_v3_incidents(status: str | None = None, owner_team: str | None = None) -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            query = session.query(SupportIncident)
            if status:
                query = query.filter(SupportIncident.status == status)
            if owner_team:
                query = query.filter(SupportIncident.owner_team == owner_team)
            return [DBRepository._v3_incident_to_dict(session, row) for row in query.order_by(desc(SupportIncident.updated_at)).all()]
        return execute_with_retry(_op)

    @staticmethod
    def get_v3_job(job_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            job = session.query(VerificationJob).filter(VerificationJob.id == job_id).first()
            return DBRepository._v3_job_to_dict(session, job) if job else None
        return execute_with_retry(_op)

    @staticmethod
    def transition_v3_job(job_id: str, target_state: str, verdict: str | None = None, technical_category: str = "") -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            job = session.query(VerificationJob).filter(VerificationJob.id == job_id).first()
            if not job:
                raise LookupError("Verification job not found")
            allowed = DBRepository._V3_JOB_TRANSITIONS.get(job.state, set())
            if target_state not in allowed:
                raise ValueError(f"Illegal verification-job transition: {job.state} -> {target_state}")
            if target_state == "completed" and verdict not in {"reproduced", "not_reproduced"}:
                raise ValueError("Completed verification jobs require a reproduced or not_reproduced verdict")
            if target_state in {"blocked", "inconclusive"} and verdict not in {target_state, None}:
                raise ValueError(f"{target_state} jobs may only use the matching verdict")
            job.state = target_state
            if target_state == "queued":
                # A bounded recovery attempt starts cleanly but retains prior
                # attempts and evidence for auditability.
                job.verdict = "pending"
                job.technical_category = ""
            elif verdict:
                job.verdict = verdict
            elif target_state in {"blocked", "inconclusive"}:
                job.verdict = target_state
            if technical_category:
                job.technical_category = redact_sensitive_data(technical_category)
            incident = session.query(SupportIncident).filter(SupportIncident.id == job.incident_id).first()
            if incident and target_state in {"completed", "blocked", "inconclusive", "failed"}:
                incident.last_outcome = job.verdict
                if job.verdict == "not_reproduced":
                    incident.status = "resolved"
                    incident.next_action = "Monitor the approved scenario for recurrence"
                    incident.resolved_at = _now_utc()
                elif job.verdict == "reproduced":
                    incident.status = "open"
                    incident.next_action = "Assign the responsible engineering team"
                elif job.verdict in {"blocked", "inconclusive"}:
                    incident.status = "blocked"
                    incident.next_action = "Review authorization, evidence, or test data"
            job.updated_at = _now_utc()
            session.flush()
            return DBRepository._v3_job_to_dict(session, job)
        return execute_with_retry(_op)

    @staticmethod
    def retry_v3_job(job_id: str) -> dict[str, Any]:
        """Queue a bounded retry without deleting prior attempts or evidence."""
        def _op(session: Session) -> dict[str, Any]:
            job = session.query(VerificationJob).filter(VerificationJob.id == job_id).first()
            if not job:
                raise LookupError("Verification job not found")
            if job.state not in {"blocked", "failed", "inconclusive"}:
                raise ValueError("Only blocked, failed, or inconclusive jobs can be retried")
            attempt_count = session.query(func.count(VerificationAttempt.id)).filter(VerificationAttempt.job_id == job_id).scalar() or 0
            if attempt_count > job.retry_limit:
                raise ValueError("Retry limit has been reached")
            job.state = "queued"
            job.verdict = "pending"
            job.technical_category = ""
            job.updated_at = _now_utc()
            session.flush()
            return DBRepository._v3_job_to_dict(session, job)
        return execute_with_retry(_op)

    # -------------------------------------------------------------------
    # 6.4 V3 approved orchestration and helpdesk metadata
    # -------------------------------------------------------------------
    @staticmethod
    def _scenario_to_dict(item: AssuranceScenario) -> dict[str, Any]:
        return {
            "scenario_id": item.id, "name": item.name, "feature": item.feature,
            "environment": item.environment, "authorized_role": item.authorized_role,
            "target_origin": item.target_origin, "scenario_type": item.scenario_type,
            "reference_id": item.reference_id, "max_actions": item.max_actions,
            "is_approved": item.is_approved,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

    @staticmethod
    def register_v3_assurance_scenario(payload: dict[str, Any]) -> dict[str, Any]:
        safe = redact_sensitive_data(payload)
        def _op(session: Session) -> dict[str, Any]:
            reference_id = safe["reference_id"]
            existing = session.query(AssuranceScenario).filter(AssuranceScenario.reference_id == reference_id).first()
            if existing:
                return DBRepository._scenario_to_dict(existing)
            if safe["scenario_type"] == "api_contract":
                source = session.query(ApiOperation).filter(ApiOperation.id == reference_id).first()
                if not source or not source.is_enabled or not source.safe_read_only:
                    raise ValueError("Scenario requires an enabled read-only API operation")
            elif safe["scenario_type"] == "data_assertion":
                source = session.query(DataAssertionScenario).filter(DataAssertionScenario.id == reference_id).first()
                if not source or not source.is_approved:
                    raise ValueError("Scenario requires an approved data assertion")
            else:
                raise ValueError("Unsupported assurance scenario type")
            item = AssuranceScenario(**safe)
            session.add(item); session.flush()
            return DBRepository._scenario_to_dict(item)
        return execute_with_retry(_op)

    @staticmethod
    def get_v3_assurance_scenario(scenario_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            item = session.query(AssuranceScenario).filter(AssuranceScenario.id == scenario_id).first()
            return DBRepository._scenario_to_dict(item) if item else None
        return execute_with_retry(_op)

    @staticmethod
    def list_v3_assurance_scenarios() -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            return [DBRepository._scenario_to_dict(item) for item in session.query(AssuranceScenario).order_by(AssuranceScenario.name).all()]
        return execute_with_retry(_op)

    @staticmethod
    def bind_v3_job_scenario(job_id: str, scenario_id: str, policy_decision: dict[str, Any]) -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            job = session.query(VerificationJob).filter(VerificationJob.id == job_id).first()
            scenario = session.query(AssuranceScenario).filter(AssuranceScenario.id == scenario_id).first()
            if not job: raise LookupError("Verification job not found")
            if not scenario: raise LookupError("Assurance scenario not found")
            if job.state not in {"created", "queued"}: raise ValueError("A scenario can only be bound before execution")
            job.scenario_id = scenario.id
            job.scenario_version = f"scenario:{scenario.id}"
            job.policy_decision = redact_sensitive_data(policy_decision)
            session.flush()
            return DBRepository._v3_job_to_dict(session, job)
        return execute_with_retry(_op)

    @staticmethod
    def create_v3_assignment_rule(payload: dict[str, Any]) -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            rule = AssignmentRule(**redact_sensitive_data(payload)); session.add(rule); session.flush()
            return {"rule_id": rule.id, "feature": rule.feature, "owner_team": rule.owner_team,
                    "severity_threshold": rule.severity_threshold, "is_enabled": rule.is_enabled}
        return execute_with_retry(_op)

    @staticmethod
    def assign_v3_incident(incident_id: str, owner_team: str, next_action: str) -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            incident = session.query(SupportIncident).filter(SupportIncident.id == incident_id).first()
            if not incident: raise LookupError("Incident not found")
            incident.owner_team = redact_sensitive_data(owner_team)
            incident.next_action = redact_sensitive_data(next_action)
            if incident.status == "open": incident.status = "assigned"
            session.flush(); return DBRepository._v3_incident_to_dict(session, incident)
        return execute_with_retry(_op)

    @staticmethod
    def auto_assign_v3_incident(incident_id: str) -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            incident = session.query(SupportIncident).filter(SupportIncident.id == incident_id).first()
            if not incident: raise LookupError("Incident not found")
            rules = session.query(AssignmentRule).filter(AssignmentRule.is_enabled.is_(True)).all()
            exact = [r for r in rules if r.feature and r.feature.casefold() == incident.affected_feature.casefold()]
            selected = (exact or rules or [None])[0]
            if selected:
                incident.owner_team = selected.owner_team
                incident.next_action = f"Investigate {incident.affected_feature or 'reported feature'}"
                if incident.status == "open": incident.status = "assigned"
            session.flush(); return DBRepository._v3_incident_to_dict(session, incident)
        return execute_with_retry(_op)

    @staticmethod
    def create_v3_monitoring_schedule(payload: dict[str, Any]) -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            scenario = session.query(AssuranceScenario).filter(AssuranceScenario.id == payload["scenario_id"]).first()
            if not scenario: raise LookupError("Assurance scenario not found")
            item = MonitoringSchedule(**redact_sensitive_data(payload)); session.add(item); session.flush()
            return {"schedule_id": item.id, "name": item.name, "scenario_id": item.scenario_id,
                    "target": item.target, "environment": item.environment, "grouping_key": item.grouping_key,
                    "interval_minutes": item.interval_minutes, "is_enabled": item.is_enabled,
                    "last_triggered_at": None}
        return execute_with_retry(_op)

    @staticmethod
    def list_v3_monitoring_schedules() -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            return [{"schedule_id": item.id, "name": item.name, "scenario_id": item.scenario_id,
                     "target": item.target, "environment": item.environment, "grouping_key": item.grouping_key,
                     "interval_minutes": item.interval_minutes, "is_enabled": item.is_enabled,
                     "last_triggered_at": item.last_triggered_at.isoformat() if item.last_triggered_at else None}
                    for item in session.query(MonitoringSchedule).order_by(MonitoringSchedule.created_at).all()]
        return execute_with_retry(_op)

    @staticmethod
    def trigger_v3_monitoring_schedule(schedule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create one durable *manual* verification job from a saved schedule.

        The method intentionally does not execute a scenario. It gives an
        operator a traceable job to review and run, without implying a
        production scheduler exists.
        """
        safe = redact_sensitive_data(payload)

        def _op(session: Session) -> dict[str, Any]:
            schedule = session.query(MonitoringSchedule).filter(MonitoringSchedule.id == schedule_id).first()
            if not schedule:
                raise LookupError("Monitoring schedule not found")
            if not schedule.is_enabled:
                raise ValueError("Monitoring schedule is disabled")
            scenario = session.query(AssuranceScenario).filter(AssuranceScenario.id == schedule.scenario_id).first()
            if not scenario or not scenario.is_approved:
                raise ValueError("Monitoring schedule requires an approved assurance scenario")

            correlation_id = f"manual-monitor-{schedule.id}-{uuid4().hex[:12]}"
            incident = (
                session.query(SupportIncident)
                .filter(
                    SupportIncident.grouping_key == schedule.grouping_key,
                    SupportIncident.last_outcome == "reproduced",
                    SupportIncident.status.in_(["open", "assigned"]),
                )
                .order_by(desc(SupportIncident.updated_at))
                .first()
            )
            grouped = incident is not None
            if not incident:
                incident = SupportIncident(
                    correlation_id=correlation_id,
                    ticket_reference=None,
                    grouping_key=schedule.grouping_key,
                    summary=f"Manual monitoring check: {schedule.name}",
                    affected_feature=scenario.feature,
                    reporter_role=safe.get("authorized_role") or scenario.authorized_role,
                    expected_behavior=safe["expected_behavior"],
                    actual_behavior=safe["actual_behavior"],
                    priority="Medium",
                    status="open",
                    owner_team="Unassigned",
                    next_action="Review and run the approved monitoring scenario",
                    last_outcome="pending",
                )
                session.add(incident)
                session.flush()
            job = VerificationJob(
                incident_id=incident.id,
                run_id=None,
                state="created",
                verdict="pending",
                scenario_id=scenario.id,
                scenario_version=f"scenario:{scenario.id}",
                authorized_role=safe.get("authorized_role") or scenario.authorized_role,
                target=schedule.target,
                mode="functional_only",
                grouping_key=schedule.grouping_key,
                retry_limit=0,
                policy_decision={"trigger": "manual_monitoring", "schedule_id": schedule.id},
            )
            session.add(job)
            schedule.last_triggered_at = _now_utc()
            session.flush()
            return {
                "schedule": {"schedule_id": schedule.id, "last_triggered_at": schedule.last_triggered_at.isoformat()},
                "grouped_into_existing_incident": grouped,
                "incident": DBRepository._v3_incident_to_dict(session, incident),
            }

        return execute_with_retry(_op)

    @staticmethod
    def add_v3_attempt(job_id: str, worker_id: str = "", state: str = "created", terminal_outcome: str = "pending", failure_category: str = "", event_sequence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        safe_events = redact_sensitive_data(event_sequence or [])
        def _op(session: Session) -> dict[str, Any]:
            job = session.query(VerificationJob).filter(VerificationJob.id == job_id).first()
            if not job:
                raise LookupError("Verification job not found")
            if job.state not in {"queued", "running"}:
                raise ValueError("Attempts can be added only while a verification job is queued or running")
            attempt_number = (session.query(func.max(VerificationAttempt.attempt_number)).filter(VerificationAttempt.job_id == job_id).scalar() or 0) + 1
            attempt = VerificationAttempt(
                job_id=job_id,
                attempt_number=attempt_number,
                worker_id=redact_sensitive_data(worker_id),
                state=state,
                terminal_outcome=terminal_outcome,
                failure_category=redact_sensitive_data(failure_category),
                event_sequence=safe_events,
                ended_at=_now_utc() if terminal_outcome != "pending" else None,
            )
            session.add(attempt)
            session.flush()
            return DBRepository._v3_attempt_to_dict(attempt)
        return execute_with_retry(_op)

    @staticmethod
    def add_v3_evidence_manifest(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe = redact_sensitive_data(payload)
        def _op(session: Session) -> dict[str, Any]:
            job = session.query(VerificationJob).filter(VerificationJob.id == job_id).first()
            if not job:
                raise LookupError("Verification job not found")
            manifest = EvidenceManifest(
                job_id=job_id,
                artifact_id=safe.get("artifact_id"),
                run_id=safe.get("run_id") or job.run_id,
                source_step=safe.get("source_step", ""),
                source_route=safe.get("source_route", ""),
                checksum=safe.get("checksum", ""),
                content_type=safe.get("content_type", "application/octet-stream"),
                storage_location=safe.get("storage_location", ""),
            )
            session.add(manifest)
            session.flush()
            return DBRepository._v3_manifest_to_dict(manifest)
        return execute_with_retry(_op)

    # -----------------------------------------------------------------------
    # 7. V3 API contract assurance (additive; no V2 execution ownership)
    # -----------------------------------------------------------------------

    @staticmethod
    def _api_operation_to_dict(operation: ApiOperation) -> dict[str, Any]:
        return {
            "operation_id": operation.id,
            "contract_id": operation.contract_id,
            "operation_key": operation.operation_key,
            "method": operation.method,
            "path": operation.path,
            "expected_status_codes": operation.expected_status_codes or [],
            "required_fields": operation.required_fields or [],
            "max_latency_ms": operation.max_latency_ms,
            "auth_requirement": operation.auth_requirement,
            "auth_fixture_ref": operation.auth_fixture_ref,
            "approved_role": operation.approved_role,
            "allow_unauthenticated_probe": operation.allow_unauthenticated_probe,
            "safe_read_only": operation.safe_read_only,
            "is_enabled": operation.is_enabled,
            "response_schema": getattr(operation, "response_schema", None) or {},
            "approved_parameters": getattr(operation, "approved_parameters", None) or [],
        }

    @staticmethod
    def _api_contract_to_dict(session: Session, contract: ApiContract) -> dict[str, Any]:
        operations = (
            session.query(ApiOperation)
            .filter(ApiOperation.contract_id == contract.id)
            .order_by(ApiOperation.operation_key)
            .all()
        )
        return {
            "contract_id": contract.id,
            "name": contract.name,
            "version": contract.version,
            "source_type": contract.source_type,
            "base_url": contract.base_url,
            "environment": contract.environment,
            "is_approved": contract.is_approved,
            "operations": [DBRepository._api_operation_to_dict(item) for item in operations],
            "created_at": contract.created_at.isoformat() if contract.created_at else None,
            "updated_at": contract.updated_at.isoformat() if contract.updated_at else None,
        }

    @staticmethod
    def register_v3_api_contract(payload: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist only normalized operations, never the source document or secrets."""
        safe = redact_sensitive_data(payload)
        safe_operations = redact_sensitive_data(operations)

        def _op(session: Session) -> dict[str, Any]:
            contract = ApiContract(
                name=safe["name"],
                version=safe.get("version", "unversioned"),
                source_type=safe["source_type"],
                base_url=safe["base_url"],
                environment=safe.get("environment", "staging"),
                is_approved=bool(safe.get("approved", False)),
            )
            session.add(contract)
            session.flush()
            valid_keys = {c.name for c in ApiOperation.__table__.columns} - {'id', 'contract_id', 'created_at'}
            for item in safe_operations:
                filtered_item = {k: v for k, v in item.items() if k in valid_keys}
                session.add(ApiOperation(contract_id=contract.id, **filtered_item))
            session.flush()
            return DBRepository._api_contract_to_dict(session, contract)

        return execute_with_retry(_op)

    @staticmethod
    def get_v3_api_contract(contract_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            contract = session.query(ApiContract).filter(ApiContract.id == contract_id).first()
            return DBRepository._api_contract_to_dict(session, contract) if contract else None
        return execute_with_retry(_op)

    @staticmethod
    def get_v3_api_operation(operation_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            operation = session.query(ApiOperation).filter(ApiOperation.id == operation_id).first()
            if not operation:
                return None
            contract = session.query(ApiContract).filter(ApiContract.id == operation.contract_id).first()
            return {
                "operation": DBRepository._api_operation_to_dict(operation),
                "contract": DBRepository._api_contract_to_dict(session, contract) if contract else None,
            }
        return execute_with_retry(_op)

    @staticmethod
    def configure_v3_api_operation(operation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe = redact_sensitive_data(payload)
        allowed = {
            "expected_status_codes", "required_fields", "response_schema", "approved_parameters",
            "max_latency_ms", "auth_requirement", "auth_fixture_ref", "approved_role",
            "allow_unauthenticated_probe", "safe_read_only", "is_enabled",
        }

        def _op(session: Session) -> dict[str, Any]:
            operation = session.query(ApiOperation).filter(ApiOperation.id == operation_id).first()
            if not operation:
                raise LookupError("API operation not found")
            for key in allowed:
                if key in safe:
                    setattr(operation, key, safe[key])
            session.flush()
            return DBRepository._api_operation_to_dict(operation)
        return execute_with_retry(_op)

    @staticmethod
    def add_v3_api_observation(payload: dict[str, Any]) -> dict[str, Any]:
        safe = redact_sensitive_data(payload)

        def _op(session: Session) -> dict[str, Any]:
            operation = session.query(ApiOperation).filter(ApiOperation.id == safe["operation_id"]).first()
            if not operation:
                raise LookupError("API operation not found")
            link_models = {
                "job_id": (VerificationJob, "Verification job"),
                "run_id": (ScanRun, "Scan run"),
                "evidence_manifest_id": (EvidenceManifest, "Evidence manifest"),
            }
            for field, (model, label) in link_models.items():
                if safe.get(field) and not session.query(model).filter(model.id == safe[field]).first():
                    raise ValueError(f"{label} link was not found")
            observation = ApiObservation(**safe)
            session.add(observation)
            session.flush()
            return DBRepository._api_observation_to_dict(observation)
        return execute_with_retry(_op)

    @staticmethod
    def _api_observation_to_dict(observation: ApiObservation) -> dict[str, Any]:
        return {
            "observation_id": observation.id,
            "operation_id": observation.operation_id,
            "job_id": observation.job_id,
            "run_id": observation.run_id,
            "evidence_manifest_id": observation.evidence_manifest_id,
            "scenario_version": observation.scenario_version,
            "result": observation.result,
            "classification": observation.classification,
            "status_code": observation.status_code,
            "latency_ms": observation.latency_ms,
            "request_metadata": observation.request_metadata or {},
            "assertion_result": observation.assertion_result or {},
            "evidence_link": observation.evidence_link,
            "created_at": observation.created_at.isoformat() if observation.created_at else None,
        }

    @staticmethod
    def list_v3_api_observations(contract_id: str) -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            return [
                DBRepository._api_observation_to_dict(item)
                for item in (
                    session.query(ApiObservation)
                    .join(ApiOperation, ApiObservation.operation_id == ApiOperation.id)
                    .filter(ApiOperation.contract_id == contract_id)
                    .order_by(ApiObservation.created_at)
                    .all()
                )
            ]
        return execute_with_retry(_op)

    @staticmethod
    def _api_plan_to_dict(row: ApiTestPlanState) -> dict[str, Any]:
        """Return the same redacted shape used by the execution service."""
        started_at = row.started_at.timestamp() if row.started_at else None
        completed_at = row.completed_at.timestamp() if row.completed_at else None
        return {
            "plan_id": row.plan_id,
            "contract_id": row.contract_id,
            "status": row.status,
            "total_operations": row.total_operations,
            "completed_count": row.completed_count,
            "passed_count": row.passed_count,
            "failed_count": row.failed_count,
            "blocked_count": row.blocked_count,
            "checkpoints": row.checkpoints or {},
            "operation_results": row.operation_results or [],
            "resolved_parameters": row.resolved_parameters or {},
            "coverage_gaps": row.coverage_gaps or [],
            "start_time": started_at,
            "end_time": completed_at,
            "duration_ms": row.duration_ms or 0,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def save_v3_api_plan(plan: dict[str, Any]) -> dict[str, Any]:
        """Upsert a redacted API plan checkpoint/state transactionally."""
        safe = redact_sensitive_data(plan)
        plan_id = str(safe.get("plan_id", ""))
        contract_id = str(safe.get("contract_id", ""))
        if not plan_id or not contract_id:
            raise ValueError("API plan and contract identifiers are required")

        def _op(session: Session) -> dict[str, Any]:
            row = session.query(ApiTestPlanState).filter(ApiTestPlanState.plan_id == plan_id).first()
            if row is None:
                row = ApiTestPlanState(plan_id=plan_id, contract_id=contract_id)
                session.add(row)
            row.contract_id = contract_id
            row.status = str(safe.get("status", row.status or "running"))
            for field in ("total_operations", "completed_count", "passed_count", "failed_count", "blocked_count"):
                if field in safe and safe[field] is not None:
                    setattr(row, field, int(safe[field]))
            for field, default in (("checkpoints", {}), ("operation_results", []), ("resolved_parameters", {}), ("coverage_gaps", [])):
                if field in safe and safe[field] is not None:
                    setattr(row, field, redact_sensitive_data(safe[field]))
            if safe.get("duration_ms") is not None:
                row.duration_ms = int(safe["duration_ms"])
            if safe.get("end_time") is not None:
                # The execution service uses a monotonic timer for duration;
                # persist a real UTC completion timestamp instead of treating
                # that monotonic value as a Unix epoch.
                row.completed_at = _now_utc()
            row.updated_at = _now_utc()
            session.flush()
            return DBRepository._api_plan_to_dict(row)

        return execute_with_retry(_op)

    @staticmethod
    def get_v3_api_plan(plan_id: str) -> dict[str, Any] | None:
        """Load a persisted API plan for progress, reporting, or resume."""
        def _op(session: Session) -> dict[str, Any] | None:
            row = session.query(ApiTestPlanState).filter(ApiTestPlanState.plan_id == plan_id).first()
            return DBRepository._api_plan_to_dict(row) if row else None
        return execute_with_retry(_op)

    # -----------------------------------------------------------------------
    # 8. V3 approved database cross-read definitions (read-only)
    # -----------------------------------------------------------------------

    @staticmethod
    def _database_query_to_dict(query: DatabaseQueryDefinition) -> dict[str, Any]:
        # Do not return SQL through the public metadata response.
        return {
            "database_query_id": query.id,
            "query_id": query.query_id,
            "name": query.name,
            "parameter_names": query.parameter_names or [],
            "database_ref": query.database_ref,
            "environment": query.environment,
            "is_approved": query.is_approved,
            "created_at": query.created_at.isoformat() if query.created_at else None,
            "updated_at": query.updated_at.isoformat() if query.updated_at else None,
        }

    @staticmethod
    def register_v3_database_query(payload: dict[str, Any]) -> dict[str, Any]:
        from api.services.v3_database_assertion_service import ApprovedDatabaseQuery, validate_approved_query

        safe = redact_sensitive_data(payload)
        definition = ApprovedDatabaseQuery(
            query_id=safe["query_id"], sql=safe["sql"],
            parameter_names=tuple(safe.get("parameter_names", [])),
            database_ref=safe.get("database_ref", "configured-test"),
            environment=safe.get("environment", "test"),
        )
        # The validator raises on any unsafe definition; successful return is
        # the approval gate for this server-owned registration.
        validate_approved_query(definition)
        if not safe.get("approved", False):
            raise ValueError("Database query must be explicitly approved before registration")

        def _op(session: Session) -> dict[str, Any]:
            if session.query(DatabaseQueryDefinition).filter(DatabaseQueryDefinition.query_id == definition.query_id).first():
                raise ValueError("Database query ID is already registered")
            row = DatabaseQueryDefinition(
                query_id=definition.query_id, name=safe["name"], sql=definition.sql,
                parameter_names=list(definition.parameter_names), database_ref=definition.database_ref,
                environment=definition.environment, is_approved=True,
            )
            session.add(row)
            session.flush()
            return DBRepository._database_query_to_dict(row)
        return execute_with_retry(_op)

    @staticmethod
    def get_v3_database_query(query_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            row = session.query(DatabaseQueryDefinition).filter(DatabaseQueryDefinition.query_id == query_id).first()
            return DBRepository._database_query_to_dict(row) if row else None
        return execute_with_retry(_op)

    @staticmethod
    def get_v3_database_query_definition(query_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            row = session.query(DatabaseQueryDefinition).filter(DatabaseQueryDefinition.query_id == query_id).first()
            if not row:
                return None
            return {
                **DBRepository._database_query_to_dict(row),
                "sql": row.sql,
            }
        return execute_with_retry(_op)

    @staticmethod
    def list_v3_database_queries() -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            rows = session.query(DatabaseQueryDefinition).order_by(DatabaseQueryDefinition.created_at.desc()).all()
            return [DBRepository._database_query_to_dict(row) for row in rows]
        return execute_with_retry(_op)

    # -----------------------------------------------------------------------
    # 9. V3 dashboard-data assurance (additive; read-only source evidence)
    # -----------------------------------------------------------------------

    @staticmethod
    def _data_scenario_to_dict(scenario: DataAssertionScenario) -> dict[str, Any]:
        return {
            "data_assertion_scenario_id": scenario.id,
            "name": scenario.name,
            "route": scenario.route,
            "ui_selector": scenario.ui_selector,
            "metric_label": scenario.metric_label,
            "source_operation_id": scenario.source_operation_id,
            "database_query_id": scenario.database_query_id,
            "source_field": scenario.source_field,
            "source_timestamp_field": scenario.source_timestamp_field,
            "expected_filters": scenario.expected_filters or {},
            "timezone": scenario.timezone,
            "freshness_window_minutes": scenario.freshness_window_minutes,
            "tolerance": scenario.tolerance,
            "value_format": scenario.value_format,
            "is_approved": scenario.is_approved,
        }

    @staticmethod
    def register_v3_data_assertion_scenario(payload: dict[str, Any]) -> dict[str, Any]:
        safe = redact_sensitive_data(payload)

        def _op(session: Session) -> dict[str, Any]:
            source = session.query(ApiOperation).filter(ApiOperation.id == safe["source_operation_id"]).first()
            if not source:
                raise LookupError("Approved source operation was not found")
            if not source.safe_read_only or not source.is_enabled:
                raise ValueError("Source operation must be an enabled read-only operation")
            database_query_id = safe.get("database_query_id")
            if database_query_id:
                query = session.query(DatabaseQueryDefinition).filter(
                    DatabaseQueryDefinition.query_id == database_query_id,
                    DatabaseQueryDefinition.is_approved.is_(True),
                ).first()
                if not query:
                    raise ValueError("Approved database query was not found")
            scenario = DataAssertionScenario(
                name=safe["name"], route=safe["route"], ui_selector=safe["ui_selector"],
                metric_label=safe.get("metric_label", ""), source_operation_id=safe["source_operation_id"],
                database_query_id=database_query_id,
                source_field=safe["source_field"], source_timestamp_field=safe.get("source_timestamp_field", ""),
                expected_filters=safe.get("expected_filters", {}), timezone=safe.get("timezone", "UTC"),
                freshness_window_minutes=safe.get("freshness_window_minutes", 60), tolerance=safe.get("tolerance", "0"),
                value_format=safe.get("value_format", "number"), is_approved=bool(safe.get("approved", False)),
            )
            session.add(scenario)
            session.flush()
            return DBRepository._data_scenario_to_dict(scenario)
        return execute_with_retry(_op)

    @staticmethod
    def get_v3_data_assertion_scenario(scenario_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            scenario = session.query(DataAssertionScenario).filter(DataAssertionScenario.id == scenario_id).first()
            return DBRepository._data_scenario_to_dict(scenario) if scenario else None
        return execute_with_retry(_op)

    @staticmethod
    def list_v3_data_assertion_scenarios() -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            rows = session.query(DataAssertionScenario).order_by(DataAssertionScenario.created_at.desc()).all()
            return [DBRepository._data_scenario_to_dict(item) for item in rows]
        return execute_with_retry(_op)

    @staticmethod
    def add_v3_data_assertion_observation(payload: dict[str, Any]) -> dict[str, Any]:
        safe = redact_sensitive_data(payload)
        # API callers may omit captured_at.  Do not pass an explicit NULL to
        # the non-null database column; preserve a truthful server capture
        # timestamp instead.
        if safe.get("captured_at") is None:
            safe["captured_at"] = datetime.now(timezone.utc)
        links = {
            "job_id": (VerificationJob, "Verification job"),
            "run_id": (ScanRun, "Scan run"),
            "evidence_manifest_id": (EvidenceManifest, "Evidence manifest"),
        }

        def _op(session: Session) -> dict[str, Any]:
            scenario = session.query(DataAssertionScenario).filter(DataAssertionScenario.id == safe["scenario_id"]).first()
            if not scenario:
                raise LookupError("Data assertion scenario not found")
            for field, (model, label) in links.items():
                if safe.get(field) and not session.query(model).filter(model.id == safe[field]).first():
                    raise ValueError(f"{label} link was not found")
            observation = DataAssertionObservation(**safe)
            session.add(observation)
            session.flush()
            return DBRepository._data_observation_to_dict(observation)
        return execute_with_retry(_op)

    @staticmethod
    def _data_observation_to_dict(observation: DataAssertionObservation) -> dict[str, Any]:
        return {
            "data_observation_id": observation.id,
            "scenario_id": observation.scenario_id,
            "job_id": observation.job_id,
            "run_id": observation.run_id,
            "evidence_manifest_id": observation.evidence_manifest_id,
            "scenario_version": observation.scenario_version,
            "result": observation.result,
            "classification": observation.classification,
            "ui_raw_value": observation.ui_raw_value,
            "source_raw_value": observation.source_raw_value,
            "normalized_ui_value": observation.normalized_ui_value,
            "normalized_source_value": observation.normalized_source_value,
            "delta": observation.delta,
            "filter_context": observation.filter_context or {},
            "source_filter_context": observation.source_filter_context or {},
            "source_fresh_at": observation.source_fresh_at.isoformat() if observation.source_fresh_at else None,
            "captured_at": observation.captured_at.isoformat() if observation.captured_at else None,
            "source_metadata": observation.source_metadata or {},
            "created_at": observation.created_at.isoformat() if observation.created_at else None,
        }

    @staticmethod
    def list_v3_data_assertion_observations(scenario_id: str) -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            rows = (
                session.query(DataAssertionObservation)
                .filter(DataAssertionObservation.scenario_id == scenario_id)
                .order_by(DataAssertionObservation.created_at)
                .all()
            )
            return [DBRepository._data_observation_to_dict(item) for item in rows]
        return execute_with_retry(_op)

    # -------------------------------------------------------------------
    # 9. V3 interactive browser sessions (additive, feature-gated by router)
    # -------------------------------------------------------------------
    @staticmethod
    def _interactive_session_to_dict(item: InteractiveBrowserSession) -> dict[str, Any]:
        return {
            "session_id": item.id,
            "run_id": item.run_id,
            "organization_id": item.organization_id,
            "owner_id": item.owner_id,
            "target_origin": item.target_origin,
            "allowed_origins": item.allowed_origins or [],
            "status": item.status,
            "phase": item.phase,
            "checkpoint_id": item.checkpoint_id,
            "module_id": item.module_id,
            "route": item.route,
            "worker_id": item.worker_id,
            "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            "last_activity_at": item.last_activity_at.isoformat() if item.last_activity_at else None,
            "metadata": redact_sensitive_data(item.metadata_json or {}),
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

    @staticmethod
    def create_interactive_session(payload: dict[str, Any]) -> dict[str, Any]:
        token_hash = str(payload.get("session_token_hash", ""))
        if len(token_hash) != 64 or any(ch not in "0123456789abcdef" for ch in token_hash.lower()):
            raise ValueError("interactive session token hash is invalid")
        safe = redact_sensitive_data(payload)

        def _op(session: Session) -> dict[str, Any]:
            item = InteractiveBrowserSession(
                id=safe["session_id"],
                run_id=safe.get("run_id"),
                organization_id=safe.get("organization_id", "default"),
                owner_id=safe.get("owner_id", "operator"),
                target_origin=safe["target_origin"],
                allowed_origins=list(safe.get("allowed_origins") or []),
                # Keep only the already-hashed opaque token.  The generic
                # redactor intentionally masks fields containing ``token``;
                # using the raw payload here avoids persisting a placeholder.
                session_token_hash=token_hash,
                status="created",
                phase="control_plane",
                checkpoint_id=safe.get("checkpoint_id"),
                module_id=safe.get("module_id"),
                route=safe.get("route"),
                worker_id=safe.get("worker_id", "local-worker"),
                expires_at=safe["expires_at"],
                metadata_json=redact_sensitive_data(safe.get("metadata") or {}),
            )
            session.add(item)
            session.flush()
            return DBRepository._interactive_session_to_dict(item)

        return execute_with_retry(_op)

    @staticmethod
    def get_interactive_session(session_id: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            item = session.query(InteractiveBrowserSession).filter(InteractiveBrowserSession.id == session_id).first()
            if not item:
                return None
            now = _now_utc()
            expires = item.expires_at
            if expires and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires and expires <= now and item.status not in {"completed", "cancelled", "failed", "expired"}:
                item.status = "expired"
                item.last_activity_at = now
                session.flush()
            return DBRepository._interactive_session_to_dict(item)
        return execute_with_retry(_op)

    @staticmethod
    def authenticate_interactive_session(session_id: str, token_hash: str) -> dict[str, Any] | None:
        def _op(session: Session) -> dict[str, Any] | None:
            item = session.query(InteractiveBrowserSession).filter(InteractiveBrowserSession.id == session_id).first()
            if not item or not hmac.compare_digest(item.session_token_hash or "", token_hash):
                return None
            expires = item.expires_at
            if expires and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires and expires <= _now_utc() and item.status not in {"completed", "cancelled", "failed", "expired"}:
                item.status = "expired"
                item.last_activity_at = _now_utc()
                session.flush()
                return None
            return DBRepository._interactive_session_to_dict(item)
        return execute_with_retry(_op)

    @staticmethod
    def transition_interactive_session(session_id: str, new_status: str, reason: str = "") -> dict[str, Any]:
        allowed = {
            "created": {"starting", "cancelled", "expired"},
            "starting": {"running", "failed", "cancelled", "expired"},
            "running": {"paused", "waiting_for_user", "completed", "failed", "cancelled", "expired"},
            "paused": {"running", "retrying", "completed", "cancelled", "expired"},
            "waiting_for_user": {"running", "retrying", "completed", "cancelled", "expired"},
            "retrying": {"running", "failed", "cancelled", "expired"},
            "failed": {"retrying", "cancelled", "expired"},
            "completed": set(), "cancelled": set(), "expired": set(),
        }

        def _op(session: Session) -> dict[str, Any]:
            item = session.query(InteractiveBrowserSession).filter(InteractiveBrowserSession.id == session_id).first()
            if not item:
                raise LookupError("Interactive browser session not found")
            if new_status != item.status and new_status not in allowed.get(item.status, set()):
                raise ValueError(f"Cannot transition interactive session from {item.status} to {new_status}")
            item.status = new_status
            item.last_activity_at = _now_utc()
            metadata = dict(item.metadata_json or {})
            if reason:
                metadata["last_transition_reason"] = redact_sensitive_data(reason)
            item.metadata_json = metadata
            session.flush()
            return DBRepository._interactive_session_to_dict(item)

        return execute_with_retry(_op)

    @staticmethod
    def touch_interactive_session(session_id: str, client_state: str = "connected") -> dict[str, Any]:
        def _op(session: Session) -> dict[str, Any]:
            item = session.query(InteractiveBrowserSession).filter(InteractiveBrowserSession.id == session_id).first()
            if not item:
                raise LookupError("Interactive browser session not found")
            if item.status in {"completed", "cancelled", "expired", "failed"}:
                return DBRepository._interactive_session_to_dict(item)
            item.last_activity_at = _now_utc()
            metadata = dict(item.metadata_json or {})
            metadata["last_client_state"] = redact_sensitive_data(client_state)
            item.metadata_json = metadata
            session.flush()
            return DBRepository._interactive_session_to_dict(item)

        return execute_with_retry(_op)

    @staticmethod
    def recover_stale_interactive_sessions(stale_before: datetime, organization_id: str | None = None) -> list[dict[str, Any]]:
        """Mark abandoned in-process workers failed without losing checkpoints.

        A process restart cannot restore a Playwright object.  Persisting a
        truthful ``failed`` state lets an operator explicitly retry from the
        saved checkpoint rather than leaving a misleading ``running`` job.
        Terminal sessions are never touched and the operation is idempotent.
        """

        def _op(session: Session) -> list[dict[str, Any]]:
            query = session.query(InteractiveBrowserSession).filter(
                InteractiveBrowserSession.status.in_(["starting", "running", "retrying"]),
                InteractiveBrowserSession.last_activity_at < stale_before,
            )
            if organization_id:
                query = query.filter(InteractiveBrowserSession.organization_id == organization_id)
            recovered: list[dict[str, Any]] = []
            for item in query.order_by(InteractiveBrowserSession.last_activity_at.asc()).all():
                item.status = "failed"
                item.phase = "recovery_required"
                item.last_activity_at = _now_utc()
                metadata = dict(item.metadata_json or {})
                metadata["recovery_reason"] = "worker_lease_expired"
                metadata["recovery_checkpoint_id"] = item.checkpoint_id or ""
                item.metadata_json = redact_sensitive_data(metadata)
                recovered.append(DBRepository._interactive_session_to_dict(item))
            session.flush()
            return recovered

        return execute_with_retry(_op)

    @staticmethod
    def mark_interactive_session_recovery_required(session_id: str) -> dict[str, Any]:
        """Explicitly convert a non-terminal session into a retryable state."""

        def _op(session: Session) -> dict[str, Any]:
            item = session.query(InteractiveBrowserSession).filter(InteractiveBrowserSession.id == session_id).first()
            if not item:
                raise LookupError("Interactive browser session not found")
            if item.status in {"completed", "cancelled", "expired"}:
                return DBRepository._interactive_session_to_dict(item)
            item.status = "failed"
            item.phase = "recovery_required"
            item.last_activity_at = _now_utc()
            metadata = dict(item.metadata_json or {})
            metadata["recovery_reason"] = "operator_requested_recovery"
            metadata["recovery_checkpoint_id"] = item.checkpoint_id or ""
            item.metadata_json = redact_sensitive_data(metadata)
            session.flush()
            return DBRepository._interactive_session_to_dict(item)

        return execute_with_retry(_op)

    @staticmethod
    def _interactive_evidence_to_dict(item: InteractiveBrowserEvidence) -> dict[str, Any]:
        return {
            "evidence_id": item.id,
            "session_id": item.session_id,
            "organization_id": item.organization_id,
            "event_type": item.event_type,
            "page_url": item.page_url,
            "route": item.route,
            "label": item.label,
            "selector": item.selector,
            "xpath": item.xpath,
            "source": item.source,
            "text_sha256": item.text_sha256,
            "text_length": item.text_length,
            "metadata": redact_sensitive_data(item.metadata_json or {}),
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }

    @staticmethod
    def add_interactive_evidence(payload: dict[str, Any]) -> dict[str, Any]:
        """Persist locator evidence without accepting raw input/page content."""

        def _op(session: Session) -> dict[str, Any]:
            item = InteractiveBrowserEvidence(
                id=payload["evidence_id"],
                session_id=payload["session_id"],
                organization_id=payload["organization_id"],
                event_type=payload["event_type"],
                page_url=payload["page_url"],
                route=payload.get("route", ""),
                label=payload.get("label", ""),
                selector=payload.get("selector", ""),
                xpath=payload.get("xpath", ""),
                source=payload.get("source", "manual_interaction"),
                text_sha256=payload.get("text_sha256", ""),
                text_length=int(payload.get("text_length", 0)),
                metadata_json=redact_sensitive_data(payload.get("metadata") or {}),
            )
            session.add(item)
            session.flush()
            return DBRepository._interactive_evidence_to_dict(item)

        return execute_with_retry(_op)

    @staticmethod
    def list_interactive_evidence(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        def _op(session: Session) -> list[dict[str, Any]]:
            rows = (
                session.query(InteractiveBrowserEvidence)
                .filter(InteractiveBrowserEvidence.session_id == session_id)
                .order_by(InteractiveBrowserEvidence.created_at.asc())
                .limit(max(1, min(limit, 500)))
                .all()
            )
            return [DBRepository._interactive_evidence_to_dict(row) for row in rows]

        return execute_with_retry(_op)

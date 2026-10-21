"""api/db/repository.py: Database repository layer for PiOne Testmate.

Provides centralized, thread-safe database operations for:
- Scan runs, modules, routes, elements, and checkpoints
- Test plans, test cases, and regression suites
- Executions, steps, artifacts, and baseline diffs
- Credential sanitization, audit logging, and optimistic concurrency
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

from api.db.models import (
    ApplicationTarget,
    Artifact,
    AuditEvent,
    Checkpoint,
    Execution,
    ExecutionStep,
    RegressionSuite,
    RegressionSuiteCase,
    RouteEdge,
    ScanElement,
    ScanModule,
    ScanRoute,
    ScanRun,
    TestCaseModel,
    TestPlan,
)
from api.db.session import execute_with_retry, get_db_session
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
                    case = session.query(TestCaseModel).filter(TestCaseModel.id == tc_id).first()
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
            base_plan = session.query(TestPlan).filter(TestPlan.run_id == baseline_run_id, TestPlan.is_baseline == True).first()
            base_cases = session.query(TestCaseModel).filter(TestCaseModel.run_id == baseline_run_id).all()
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

            return {
                "current_run_id": current_run_id,
                "baseline_run_id": baseline_run_id,
                "baseline_plan_id": base_plan.id if base_plan else None,
                "newly_passing": newly_passing,
                "newly_failing": newly_failing,
                "removed_routes": removed_routes,
                "removed_elements": removed_elements,
                "changed_locators": changed_locators,
                "changed_expected": changed_expected,
                "unchanged_count": len(set(base_by_tcid) & set(curr_by_tcid)) - len(newly_failing) - len(newly_passing),
            }

        return execute_with_retry(_op)

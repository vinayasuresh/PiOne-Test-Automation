"""POST /scan router — the main API endpoint."""

import asyncio
from datetime import datetime, timezone
import json
import shutil
import os
import platform
import re
from pathlib import Path
from uuid import uuid4

from typing import Any
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from automation_framework.crawler.auth_exceptions import (
    AuthenticationExpiredError,
    DiscoveryUnavailableError,
    LoginFailedError,
)
from automation_framework.utils.redactor import redact_sensitive_data

_RUN_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
ALLOWED_ARTIFACT_TYPES = {
    "json",
    "yaml",
    "markdown",
    "excel",
    "execution_results",
    "checkpoint",
    "screenshots",
    "log",
}

from api.models.scan_models import (
    ErrorResponse,
    ExecuteTestRequest,
    ExecuteTestResponse,
    GenerateTestRequest,
    GeneratedTestResponse,
    ScanRequest,
    ScanResponse,
    ModuleDiscoveryRequest,
    ModuleDiscoveryResponse,
    RefreshSessionRequest,
    RefreshSessionResponse,
    RegressionRunRequest,
    RegressionRunResponse,
    RerunStepRequest,
    RerunStepResponse,
    RouteReplayRequest,
    ScanSummary,
    SessionStatusResponse,
    SystemInfoResponse,
    LLMProviderInfo,
    ScanControlRequest,
    ScanControlResponse,
    ArtifactStatusResponse,
    CompareBaselineRequest,
    CreateRegressionSuiteRequest,
    CreateRunRequest,
    ExecuteSelectedRegressionRequest,
    ResumeRunRequest,
    SaveBaselineRequest,
    SaveProgressRequest,
    UpdateRegressionSuiteRequest,
    TestContextResponse,
    TestCase,
    TestStep,
)
from api.db.repository import DBRepository
from api.db.session import check_db_status
from api.services import crawler_service, mock_service
from api.services.auth_lifecycle_service import (
    check_auth_session_validity,
    refresh_auth_session,
)
from api.services import browser_readiness
from api.services.llm_provider_gateway import LLMProviderGateway
from api.services.llm_service import provider_catalog, provider_status, request_plan, test_provider_connection
from api.services.run_context_service import (
    create_run_context,
    delete_run_context,
    get_run_context,
    list_saved_runs,
    save_run_context,
)
from api.services.run_state_reconciliation import calculate_module_progress, calculate_route_progress
from api.services.scan_event_bus import get_channel, get_latest_telemetry, publish
from api.services.test_context_service import create_context, decode_upload, load_context
from api.services.test_execution_service import (
    auth_session_path,
    execute_test_cases,
    load_saved_test_cases,
    rerun_step,
)
from api.services.test_input_normalizer import TestInputNormalizer
from api.services.test_planning_service import build_test_cases
from automation_framework.utils.logger import logger
from automation_framework.config.settings import LOG_PATH, REPORT_PATH
from api.services.scan_control_service import (
    finish_scan,
    is_scan_active,
    pause_scan,
    resume_scan,
    is_scan_paused,
    stop_scan,
    set_user_decision,
    try_start_scan,
)

router = APIRouter()

# Long-running provider calls (especially local Ollama models) must not keep
# a tunnel request open until the model finishes or times out.  Results are
# kept in-process briefly; the generated plan itself is still persisted by
# the existing generation flow.
_BACKGROUND_PLAN_JOBS: dict[str, dict[str, Any]] = {}
_BACKGROUND_EXECUTION_JOBS: dict[str, dict[str, Any]] = {}

# Remote browsers (for example, a colleague using LocalTunnel) must not hold
# one HTTP request open for the full Playwright crawl.  The tunnel can expire
# that request even though the backend scan is still healthy.  Jobs are kept
# only as a lightweight in-process guard; durable progress remains in
# RunContext/checkpoints and the existing telemetry channel.
_BACKGROUND_SCAN_TASKS: set[asyncio.Task] = set()


def _persist_execution_job_state(
    run_id: str | None,
    job_id: str,
    status: str,
    *,
    selected_count: int | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """Keep an execution job recoverable after an app reload."""
    if not run_id:
        return
    run_ctx = get_run_context(run_id)
    if not run_ctx:
        return
    metadata: dict[str, Any] = {
        "job_id": job_id,
        "status": status,
        "selected_count": selected_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        metadata["error"] = redact_sensitive_data(str(error))
    if result:
        metadata["execution_id"] = result.get("execution_id")
    run_ctx.artifact_paths["async_execution_job"] = metadata
    if status == "running":
        run_ctx.status = "executing"
    save_run_context(run_ctx)


def _recover_execution_job(job_id: str) -> dict[str, Any] | None:
    """Recover a terminal selected-execution job from durable run state."""
    context_dir = Path(REPORT_PATH) / "run_contexts"
    for path in context_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            artifacts = payload.get("artifact_paths") or {}
            metadata = artifacts.get("async_execution_job") or {}
            if metadata.get("job_id") != job_id:
                continue
            if metadata.get("status") == "failed":
                return {
                    "status": "failed",
                    "error": metadata.get("error") or "Execution failed before completion.",
                    "http_status": 500,
                }
            rows = payload.get("execution_results")
            if metadata.get("status") == "completed" or (payload.get("status") == "executed" and rows is not None):
                summary = payload.get("execution_summary") or {}
                return {
                    "status": "completed",
                    "result": {
                        "status": "success",
                        "run_id": payload.get("run_id"),
                        "execution_id": artifacts.get("execution_id"),
                        "selected_test_count": metadata.get("selected_count") or 0,
                        "executed_test_count": len({row.get("TC_ID") for row in rows if row.get("TC_ID")}),
                        "total_steps": len(rows),
                        "summary": summary,
                        "rows": rows,
                        "artifacts": artifacts,
                    },
                }
            return {
                "status": "failed",
                "error": "Execution monitoring was interrupted by an app restart before results were saved. Retry the selected tests.",
                "http_status": 503,
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


async def _run_scan_in_background(request: ScanRequest) -> None:
    try:
        await scan(request)
    except Exception as exc:
        # The crawler service normally persists terminal/auth-required state.
        # If validation/browser setup fails before the crawler owns the run,
        # persist a terminal state so the remote client does not poll forever.
        try:
            run_id = request.run_id or request.resume_run_id or request.scan_id
            if run_id:
                run_ctx = get_run_context(run_id)
                if not run_ctx:
                    run_ctx = create_run_context(
                        target_origin=request.url,
                        allowed_path_namespace="/",
                        run_id=run_id,
                        auth_session_id=request.auth_session_id,
                        username=request.username,
                        display_name=request.session_name,
                    )
                if run_ctx.status == "running":
                    run_ctx.status = "failed"
                    run_ctx.error_message = f"{type(exc).__name__}: {exc}"
                    save_run_context(run_ctx)
        except Exception:
            logger.debug("Unable to persist background scan failure", exc_info=True)
        # Prevent an unhandled task exception from being logged as an
        # application failure after the client has already received 202.
        logger.debug("Background scan task ended for %s: %s", request.run_id or request.scan_id, exc)


def _forget_background_task(task: asyncio.Task) -> None:
    _BACKGROUND_SCAN_TASKS.discard(task)


@router.post("/scan/start", summary="Start a scan without holding the HTTP request open")
async def start_scan_async(request: ScanRequest) -> dict[str, Any]:
    """Launch the existing scan flow and return immediately.

    This endpoint is specifically for remote/tunnel clients.  The original
    ``POST /scan`` remains available for local callers and keeps its existing
    response contract.
    """
    run_id = request.run_id or request.resume_run_id or request.scan_id or uuid4().hex
    request.run_id = run_id
    request.scan_id = request.scan_id or run_id
    task = asyncio.create_task(_run_scan_in_background(request))
    _BACKGROUND_SCAN_TASKS.add(task)
    task.add_done_callback(_forget_background_task)
    return {"run_id": run_id, "scan_id": request.scan_id, "status": "started"}


@router.post(
    "/discover-modules",
    response_model=ModuleDiscoveryResponse,
    summary="Authenticate and list modules before scanning",
)
async def discover_modules(request: ModuleDiscoveryRequest) -> Any:
    """Return the live application's current menu modules without crawling them."""
    correlation_id = str(uuid4())
    browser_status = browser_readiness.check_browser_readiness()
    if not browser_status.get("ready"):
        msg = browser_status.get("error") or f"Playwright Chromium executable is missing. {browser_readiness.BROWSER_INSTALL_HINT}"
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "category": "DISCOVERY_UNAVAILABLE",
                    "message": msg,
                    "correlation_id": correlation_id,
                },
                "category": "DISCOVERY_UNAVAILABLE",
                "message": msg,
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    try:
        result = await asyncio.to_thread(
            crawler_service.discover_modules, request.url, request.username, request.password,
        )
        message = (
            "Choose one or more modules to scan. Only those modules and their "
            "safe in-module navigation will be explored."
        )
        resp = ModuleDiscoveryResponse(**result, message=message, correlation_id=correlation_id)
        return JSONResponse(
            status_code=200,
            content=resp.model_dump(mode="json"),
            headers={"X-Correlation-ID": correlation_id},
        )
    except AuthenticationExpiredError as exc:
        return JSONResponse(
            status_code=401,
            content={
                "detail": {
                    "category": "AUTHENTICATION_EXPIRED",
                    "message": "Authentication expired. Refresh the session or start a new scan.",
                    "correlation_id": correlation_id,
                },
                "category": "AUTHENTICATION_EXPIRED",
                "message": "Authentication expired. Refresh the session or start a new scan.",
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    except LoginFailedError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "category": "LOGIN_FAILED",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                },
                "category": "LOGIN_FAILED",
                "message": str(exc),
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    except DiscoveryUnavailableError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "detail": {
                    "category": "DISCOVERY_UNAVAILABLE",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                },
                "category": "DISCOVERY_UNAVAILABLE",
                "message": str(exc),
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "category": "VALIDATION_ERROR",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                },
                "category": "VALIDATION_ERROR",
                "message": str(exc),
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    except Exception as exc:
        logger.exception("[API] Module discovery failed")
        return JSONResponse(
            status_code=502,
            content={
                "detail": {
                    "category": "DISCOVERY_UNAVAILABLE",
                    "message": f"Module discovery could not complete: {exc}",
                    "correlation_id": correlation_id,
                },
                "category": "DISCOVERY_UNAVAILABLE",
                "message": f"Module discovery could not complete: {exc}",
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )


@router.post(
    "/session/refresh",
    response_model=RefreshSessionResponse,
    summary="Refresh an expired authentication session in-place",
)
async def refresh_session(request: RefreshSessionRequest) -> Any:
    correlation_id = str(uuid4())
    if not request.run_id or not request.run_id.strip():
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "category": "VALIDATION_ERROR",
                    "message": "run_id is required to refresh an existing session.",
                    "correlation_id": correlation_id,
                },
                "category": "VALIDATION_ERROR",
                "message": "run_id is required to refresh an existing session.",
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    if not request.username or not request.password:
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "category": "VALIDATION_ERROR",
                    "message": "Username and password are required to refresh the session.",
                    "correlation_id": correlation_id,
                },
                "category": "VALIDATION_ERROR",
                "message": "Username and password are required to refresh the session.",
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    try:
        result = await asyncio.to_thread(
            refresh_auth_session,
            url=request.url,
            username=request.username,
            password=request.password,
            auth_session_id=None,
            run_id=request.run_id,
        )
        resp = RefreshSessionResponse(
            status="success",
            run_id=result["run_id"],
            auth_session_id=result["auth_session_id"],
            message="Session refreshed successfully. You can continue scanning or execution.",
        )
        return JSONResponse(
            status_code=200,
            content=resp.model_dump(mode="json"),
            headers={"X-Correlation-ID": correlation_id},
        )
    except LoginFailedError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "category": "LOGIN_FAILED",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                },
                "category": "LOGIN_FAILED",
                "message": str(exc),
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    except DiscoveryUnavailableError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "detail": {
                    "category": "DISCOVERY_UNAVAILABLE",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                },
                "category": "DISCOVERY_UNAVAILABLE",
                "message": str(exc),
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "category": "VALIDATION_ERROR",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                },
                "category": "VALIDATION_ERROR",
                "message": str(exc),
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )
    except Exception as exc:
        logger.exception("[API] Session refresh failed")
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "category": "SERVER_ERROR",
                    "message": f"Failed to refresh session: {exc}",
                    "correlation_id": correlation_id,
                },
                "category": "SERVER_ERROR",
                "message": f"Failed to refresh session: {exc}",
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-ID": correlation_id},
        )


@router.get(
    "/session/status/{run_id}",
    response_model=SessionStatusResponse,
    summary="Check session status for a run",
)
async def session_status(run_id: str) -> SessionStatusResponse:
    run_ctx = get_run_context(run_id)
    if not run_ctx:
        raise HTTPException(status_code=404, detail=f"Run context '{run_id}' not found")
    if not run_ctx.auth_session_id:
        return SessionStatusResponse(
            run_id=run_id,
            auth_session_id=None,
            is_valid=False,
            status="missing",
            detail="No authentication session associated with this run.",
        )
    is_valid, reason = await asyncio.to_thread(
        check_auth_session_validity,
        run_ctx.target_origin,
        run_ctx.auth_session_id,
    )
    status_str = "authenticated" if is_valid else "expired"
    return SessionStatusResponse(
        run_id=run_id,
        auth_session_id=run_ctx.auth_session_id,
        is_valid=is_valid,
        status=status_str,
        detail=f"Session is {status_str}: {reason}",
    )


@router.post(
    "/rerun-step",
    response_model=RerunStepResponse,
    summary="Rerun a single test step",
)
async def rerun_single_step(request: RerunStepRequest) -> RerunStepResponse:
    try:
        result = await asyncio.to_thread(
            rerun_step,
            base_url=request.base_url,
            test_case=request.test_case,
            step_id=request.step_id,
            auth_session_id=request.auth_session_id,
            run_id=request.run_id,
        )
        return RerunStepResponse(**result)
    except AuthenticationExpiredError as exc:
        raise HTTPException(
            status_code=401,
            detail="Authentication expired. Refresh the session to continue.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/scan",
    response_model=ScanResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request (bad URL, etc.)"},
        401: {"model": ErrorResponse, "description": "Authentication expired"},
        422: {"model": ErrorResponse, "description": "Login failed"},
        500: {"model": ErrorResponse, "description": "Internal scan error"},
    },
    summary="Scan a web application",
    description=(
        "Crawls the target URL, extracts routes and UI components, and "
        "returns a structured response. Pass `mock: true` for instant demo "
        "data (no browser launched). The endpoint always returns a JSON "
        "body — `status` will be `success`, `partial_success`, `timeout`, "
        "or `error`."
    ),
)
async def scan(request: ScanRequest) -> ScanResponse:
    logger.info(
        f"POST /scan | url={request.url} | mock={request.mock} | "
        f"has_credentials={bool(request.username)}"
    )

    # ──────────────────────────────────────────────────────────────────
    # Mock mode — instant response, no browser involved.
    # ──────────────────────────────────────────────────────────────────
    if request.mock:
        logger.info("[API] Mock mode: returning pre-defined sample data")
        mock_resp = mock_service.get_mock_response(request.url)
        mock_run_id = request.run_id or request.scan_id or f"mock_{uuid4().hex[:12]}"
        mock_resp.run_id = mock_run_id

        # Build page_intelligence dictionary for mock export
        page_intelligence = {}
        for r in mock_resp.routes:
            buttons = [{"label": b, "tag": "button", "role": "button", "selector": f"button:has-text('{b}')"} for b in r.components.buttons]
            inputs = [{"label": inp, "tag": "input", "role": "textbox", "selector": f"input[name='{inp.lower()}']"} for inp in r.components.inputs]
            dropdowns = [{"label": d, "tag": "select", "role": "combobox", "selector": f"select[name='{d.lower()}']"} for d in r.components.dropdowns]
            page_intelligence[r.path] = {
                "route": r.path,
                "url": f"{request.url.rstrip('/')}{r.path}",
                "route_path": r.path,
                "menu_name": r.menu_name,
                "route_status": "WORKING",
                "route_score": 100,
                "dom_stable": True,
                "buttons": buttons,
                "inputs": inputs,
                "dropdowns": dropdowns,
                "tables": r.components.tables,
                "charts": r.components.charts,
                "interactions": [i.model_dump() for i in r.interactions],
            }
        crawl_data = {
            "run_id": mock_run_id,
            "target_url": request.url,
            "page_intelligence": page_intelligence,
            "interaction_flow": [],
            "parking_lot_actions": [],
            "scan_events": [],
            "summary": mock_resp.summary.model_dump() if mock_resp.summary else {},
        }
        try:
            crawler_service.export_crawl_results(crawl_data, run_id=mock_run_id)
            checkpoint_file = REPORT_PATH / f"checkpoint_{mock_run_id}.json"
            checkpoint_file.write_text(json.dumps(crawl_data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("[API] Mock export note: %s", exc)

        try:
            run_ctx = get_run_context(mock_run_id)
            if not run_ctx:
                run_ctx = create_run_context(
                    target_origin=request.url,
                    allowed_path_namespace="/",
                    run_id=mock_run_id,
                    auth_session_id=request.auth_session_id or f"mock_auth_{uuid4().hex[:8]}",
                    display_name=request.session_name,
                )
            run_ctx.scan_results = mock_resp.model_dump()
            run_ctx.selected_modules = request.selected_module_ids or ["mod_inventory", "mod_cart", "mod_checkout"]
            run_ctx.status = "scanned"
            save_run_context(run_ctx)
            from api.db.repository import DBRepository
            DBRepository.save_run(run_ctx.model_dump())
        except Exception as exc:
            logger.debug("[API] Failed to initialize mock run context: %s", exc)
        return mock_resp

    if not request.mock:
        browser_status = browser_readiness.check_browser_readiness()
        if not browser_status.get("ready"):
            msg = browser_status.get("error") or f"Playwright Chromium executable is missing. {browser_readiness.BROWSER_INSTALL_HINT}"
            raise HTTPException(status_code=503, detail=msg)

    scan_id = request.scan_id or uuid4().hex
    effective_run_id = request.run_id or request.resume_run_id or scan_id

    existing_ctx = get_run_context(effective_run_id)
    # A retry may arrive from a refreshed browser tab before its in-memory
    # auth_session_id is updated. Reuse the run-bound session as the safe
    # fallback; never require the user to start the crawl over.
    effective_auth_session_id = request.auth_session_id or (
        existing_ctx.auth_session_id if existing_ctx else None
    )
    if existing_ctx and existing_ctx.status in {"running", "paused"} and is_scan_active(effective_run_id):
        raise HTTPException(
            status_code=409,
            detail=f"Run '{effective_run_id}' is already active. Reconnect to the existing run state.",
        )

    if not try_start_scan(effective_run_id):
        raise HTTPException(
            status_code=409,
            detail=f"Run '{effective_run_id}' is already active. Reconnect to the existing run state.",
        )

    try:
        run_ctx = existing_ctx or create_run_context(
            target_origin=request.url,
            allowed_path_namespace="/",
            run_id=effective_run_id,
            auth_session_id=effective_auth_session_id,
            username=request.username,
            display_name=request.session_name,
        )
        run_ctx.scan_id = scan_id
        if effective_auth_session_id:
            run_ctx.auth_session_id = effective_auth_session_id
        run_ctx.selected_modules = request.selected_module_ids or run_ctx.selected_modules
        run_ctx.status = "running"
        save_run_context(run_ctx)
    except Exception as exc:
        finish_scan(effective_run_id)
        raise HTTPException(status_code=500, detail=f"Failed to initialize run context: {exc}") from exc

    get_channel(scan_id)
    publish(scan_id, {"type": "scan_started", "scan_id": scan_id, "run_id": effective_run_id, "url": request.url})

    # ──────────────────────────────────────────────────────────────────
    # Real mode — run the synchronous Playwright crawler in a worker thread.
    # ──────────────────────────────────────────────────────────────────
    try:
        result: ScanResponse = await asyncio.to_thread(
            crawler_service.run_scan,
            request.url,
            request.username,
            request.password,
            request.interactive_mode,
            request.interactive_timeout,
            request.resume_run_id,
            scan_id,
            None,
            request.selected_module_ids,
            effective_auth_session_id,
            effective_run_id,
        )
        return result

    except AuthenticationExpiredError as exc:
        logger.warning(f"[API] Scan blocked by expired auth: {exc}")
        raise HTTPException(
            status_code=401,
            detail="Authentication expired. Refresh the session to continue.",
        ) from exc

    except RuntimeError as exc:
        # Login failures and other deterministic errors raised by the crawler.
        logger.error(f"[API] Scan rejected: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))

    except ValueError as exc:
        logger.error(f"[API] Bad scan request: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))

    except Exception as exc:
        logger.exception(f"[API] Unexpected scan error: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Scan failed: {type(exc).__name__}: {exc}",
        )
    finally:
        finish_scan(effective_run_id)


@router.post("/replay-route", response_model=ScanResponse, summary="Replay and compare one scanned route")
async def replay_route(request: RouteReplayRequest) -> ScanResponse:
    scan_id = request.scan_id or uuid4().hex
    get_channel(scan_id)
    publish(scan_id, {"type": "scan_started", "scan_id": scan_id,
                      "url": request.route, "mode": "route_replay"})
    try:
        return await asyncio.to_thread(
            crawler_service.run_scan,
            request.base_url,
            request.username,
            request.password,
            False,
            0,
            request.checkpoint_id,
            scan_id,
            request.route,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/generate-test",
    response_model=GeneratedTestResponse,
    summary="Generate a test script from scan intelligence",
)
async def generate_test(request: GenerateTestRequest) -> GeneratedTestResponse:
    routes = request.scan.routes or []
    if not routes:
        raise HTTPException(status_code=400, detail="Scan data has no routes to test")

    context = load_context(request.context_id)
    combined_prompt = (request.user_prompt or context.get("user_prompt") or request.description or "").strip()

    # 1. Normalize requirements & test cases
    normalized_items = TestInputNormalizer.normalize_all(
        context.get("requirements", ""),
        context.get("testcases", ""),
        combined_prompt,
    )
    requirements_list = [item.to_dict() for item in normalized_items if item.item_type == "requirement"]
    testcases_list = [item.to_dict() for item in normalized_items if item.item_type == "testcase"]

    # 2. Plan tests through the LLMProviderGateway
    gateway = LLMProviderGateway()
    scan_dict = request.scan.model_dump()
    try:
        plan_result = await asyncio.to_thread(
            gateway.plan_tests,
            request.provider,
            request.model,
            scan_dict,
            requirements_list,
            testcases_list,
            combined_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 3. Assemble validated TestCase objects
    raw_cases = plan_result.get("test_cases", [])
    test_cases: list[TestCase] = []
    for rc in raw_cases:
        try:
            steps = [
                TestStep(
                    step_id=s.get("step_id", ""),
                    action=s.get("action", "assert_visible"),
                    route=s.get("route", ""),
                    target_label=s.get("target_label", ""),
                    selector=s.get("selector") or {},
                    expected_result=s.get("expected_result", ""),
                    value=s.get("value", ""),
                    element_identity=s.get("element_identity", ""),
                    safety_classification=s.get("safety_classification", ""),
                )
                for s in rc.get("steps", [])
            ]
            ordinal = len(test_cases) + 1
            proposed_id = str(rc.get("tc_id") or "").strip() or f"TC_{ordinal:03d}"
            used_ids = {case.tc_id for case in test_cases}
            if proposed_id in used_ids:
                proposed_id = f"TC_{ordinal:03d}"
            test_cases.append(TestCase(
                tc_id=proposed_id,
                title=rc.get("title", ""),
                module=rc.get("module", ""),
                route=rc.get("route", ""),
                purpose=rc.get("purpose", ""),
                requirement_references=rc.get("requirement_references", []),
                steps=steps,
                screen=rc.get("screen") or rc.get("module", ""),
                feature=rc.get("feature") or rc.get("module", ""),
                testcase=rc.get("testcase") or rc.get("title", ""),
                steps_to_execute=rc.get("steps_to_execute", ""),
                severity=rc.get("severity", "Medium"),
                test_type=rc.get("test_type", "positive"),
                source_references=rc.get("source_references", []),
                notes_reason=rc.get("notes_reason", ""),
                tested_version=rc.get("tested_version", ""),
            ))
        except Exception:
            continue

    if not test_cases:
        test_cases = build_test_cases(request.scan, context)

    script = _build_pytest_script(
        base_url=request.base_url,
        scan=request.scan,
        description=combined_prompt,
        feature_focus=request.feature_focus or "",
        test_cases=test_cases,
    )

    metadata = plan_result.get("planning_metadata", {})
    metadata["scan_context_attached"] = True
    metadata["scan_routes"] = len(request.scan.routes)
    metadata["scan_targets"] = sum(len(r.automation_targets or []) for r in request.scan.routes)
    metadata["uploaded_sources"] = context.get("source_files", [])
    metadata["normalized_requirements"] = len(requirements_list)
    metadata["normalized_testcases"] = len(testcases_list)
    metadata["testcase_template"] = [
        "Testcase Id", "Screen", "Feature", "Testcase", "Steps to Execute",
        "Expected Result", "Test Status", "Actual Result", "Severity", "BUG Status",
        "Notes / Reason", "Bug sheet Current Version", "Tested Version",
    ]
    run_id = request.run_id or request.scan.run_id
    if run_id:
        try:
            run_ctx = get_run_context(run_id)
            if not run_ctx:
                run_ctx = create_run_context(
                    target_origin=request.base_url or "https://www.saucedemo.com",
                    allowed_path_namespace="/",
                    run_id=run_id,
                    status="planned",
                )
            if run_ctx:
                run_ctx.generated_test_plan = [tc.model_dump() for tc in test_cases]
                run_ctx.generated_script = script
                run_ctx.status = "planned"
                save_run_context(run_ctx)
            from api.db.repository import DBRepository
            DBRepository.save_test_cases(run_id, [tc.model_dump() for tc in test_cases])
        except Exception as exc:
            logger.debug("[API] Failed to save test cases/plan for run %s: %s", run_id, exc)

    return GeneratedTestResponse(
        status="success",
        run_id=run_id,
        script=script,
        execution_command="pytest generated_tests/test_generated_ui_flow.py",
        planning_notes=metadata.get("fallback_reason") or f"Planned using {metadata.get('provider')} ({metadata.get('plan_type')})",
        context_id=request.context_id,
        test_cases=test_cases,
        planning_metadata=metadata,
        coverage_gaps=plan_result.get("coverage_gaps", []),
        provider=plan_result.get("provider", metadata.get("provider", "deterministic")),
        model=plan_result.get("model", metadata.get("model", "")),
        fallback_used=plan_result.get("fallback_used", metadata.get("fallback_used", False)),
        warnings=plan_result.get("warnings", []),
    )


async def _run_generate_test_job(job_id: str, request: GenerateTestRequest) -> None:
    try:
        result = await generate_test(request)
        _BACKGROUND_PLAN_JOBS[job_id] = {"status": "completed", "result": result.model_dump(mode="json")}
    except HTTPException as exc:
        _BACKGROUND_PLAN_JOBS[job_id] = {
            "status": "failed",
            "error": exc.detail,
            "http_status": exc.status_code,
        }
    except Exception as exc:
        logger.exception("Background test generation failed for %s", job_id)
        _BACKGROUND_PLAN_JOBS[job_id] = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "http_status": 500,
        }


@router.post("/generate-test/start", summary="Start test generation without holding the HTTP request open")
async def start_generate_test_async(request: GenerateTestRequest) -> dict[str, str]:
    job_id = uuid4().hex
    _BACKGROUND_PLAN_JOBS[job_id] = {"status": "running"}
    task = asyncio.create_task(_run_generate_test_job(job_id, request))
    task.add_done_callback(lambda _: None)
    return {"job_id": job_id, "status": "started"}


@router.get("/generate-test/status/{job_id}", summary="Get asynchronous test generation status")
async def get_generate_test_status(job_id: str) -> dict[str, Any]:
    if not _RUN_ID_REGEX.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Invalid generation job ID")
    job = _BACKGROUND_PLAN_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return job



@router.get("/llm/providers", response_model=list[LLMProviderInfo], summary="List configured test-planning providers")
async def list_llm_providers() -> list[LLMProviderInfo]:
    return [LLMProviderInfo(**item) for item in provider_catalog()]


@router.post("/llm/test-connection", summary="Test connection to an AI provider")
async def test_llm_connection(provider: str) -> dict:
    return await asyncio.to_thread(test_provider_connection, provider)


@router.get("/llm/provider-status", summary="Safe provider diagnostics")
async def llm_provider_status(provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    return await asyncio.to_thread(provider_status, provider, model)


@router.post("/test-context/upload", response_model=TestContextResponse, summary="Store test inputs for the current test-generation session")
async def upload_test_context(
    requirements_file: UploadFile | None = File(None),
    testcases_file: UploadFile | None = File(None),
    user_prompt: str = Form(""),
) -> TestContextResponse:
    """Accept text-based requirements and testcase artifacts plus a short prompt."""
    requirements_text = ""
    testcases_text = ""
    source_files: list[str] = []
    try:
        if requirements_file:
            requirements_text, filename = decode_upload(
                await requirements_file.read(), requirements_file.filename,
            )
            source_files.append(f"requirements:{filename}")
        if testcases_file:
            testcases_text, filename = decode_upload(
                await testcases_file.read(), testcases_file.filename,
            )
            source_files.append(f"testcases:{filename}")
        return TestContextResponse(**create_context(
            requirements_text, testcases_text, user_prompt, source_files,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/execute-test",
    response_model=ExecuteTestResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Authentication expired"},
    },
    summary="Execute generated test plan against current scan intelligence",
)
async def execute_test(request: ExecuteTestRequest) -> ExecuteTestResponse:
    if not request.script.strip():
        raise HTTPException(status_code=400, detail="Generated script is required")
    test_cases = request.test_cases or build_test_cases(request.scan, {})
    if not test_cases:
        raise HTTPException(status_code=400, detail="No executable safe checks were derived from this scan")

    effective_session_id = request.auth_session_id or request.scan.auth_session_id
    run_id = request.run_id or request.scan.run_id

    try:
        rows, summary, artifacts = await asyncio.to_thread(
            execute_test_cases,
            request.base_url,
            test_cases,
            request.allow_mutating_actions,
            None,
            effective_session_id,
            run_id,
        )
        # Persist the ordinary execution exactly like selective/regression
        # execution.  Without this, the first run only existed in browser
        # memory and disappeared from Saved Runs, suites, and report reloads.
        if run_id:
            try:
                from api.db.repository import DBRepository
                exec_id = artifacts.get("execution_id", uuid4().hex)
                DBRepository.record_execution(
                    execution_id=exec_id,
                    run_id=run_id,
                    status="completed",
                    rows=[r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rows],
                    summary=summary.model_dump(),
                )
                run_ctx = get_run_context(run_id)
                if run_ctx:
                    run_ctx.execution_results = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rows]
                    run_ctx.execution_summary = summary.model_dump()
                    run_ctx.artifact_paths.update(artifacts)
                    run_ctx.status = "executed"
                    save_run_context(run_ctx)
            except Exception as persist_exc:
                logger.warning("[API] Execution completed but persistence failed for %s: %s", run_id, persist_exc)
        return ExecuteTestResponse(run_id=run_id, rows=rows, summary=summary, artifacts=artifacts)
    except AuthenticationExpiredError as exc:
        logger.warning(f"[API] Execution blocked by expired auth: {exc}")
        raise HTTPException(
            status_code=401,
            detail="Authentication expired. Refresh the session to continue.",
        ) from exc


async def _run_execute_test_job(job_id: str, request: ExecuteTestRequest) -> None:
    try:
        result = await execute_test(request)
        _BACKGROUND_EXECUTION_JOBS[job_id] = {
            "status": "completed",
            "result": jsonable_encoder(result),
        }
    except HTTPException as exc:
        _BACKGROUND_EXECUTION_JOBS[job_id] = {
            "status": "failed",
            "error": exc.detail,
            "http_status": exc.status_code,
        }
    except Exception as exc:
        logger.exception("Background execution failed for %s", job_id)
        _BACKGROUND_EXECUTION_JOBS[job_id] = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "http_status": 500,
        }


@router.post("/execute-test/start", summary="Start test execution without holding the HTTP request open")
async def start_execute_test_async(request: ExecuteTestRequest) -> dict[str, str]:
    job_id = uuid4().hex
    _BACKGROUND_EXECUTION_JOBS[job_id] = {"status": "running"}
    task = asyncio.create_task(_run_execute_test_job(job_id, request))
    task.add_done_callback(lambda _: None)
    return {"job_id": job_id, "status": "started"}


@router.get("/execute-test/status/{job_id}", summary="Get asynchronous test execution status")
async def get_execute_test_status(job_id: str) -> dict[str, Any]:
    if not _RUN_ID_REGEX.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Invalid execution job ID")
    job = _BACKGROUND_EXECUTION_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Execution job not found")
    return job


@router.post("/regression-run", response_model=RegressionRunResponse, summary="Rerun a saved controlled test plan")
async def regression_run(request: RegressionRunRequest) -> RegressionRunResponse:
    execution_id = request.execution_id
    run_id = request.run_id
    run_ctx = get_run_context(run_id) if run_id else None

    if not execution_id and run_ctx and run_ctx.artifact_paths.get("execution_id"):
        execution_id = run_ctx.artifact_paths["execution_id"]
    if not execution_id and run_ctx and run_ctx.artifact_paths.get("directory"):
        execution_id = Path(run_ctx.artifact_paths["directory"]).name
    if not execution_id and run_ctx and run_ctx.artifact_paths:
        for v in run_ctx.artifact_paths.values():
            for part in Path(str(v)).parts:
                if part.startswith("execution_"):
                    execution_id = part
                    break
            if execution_id:
                break

    if not execution_id:
        raise HTTPException(status_code=400, detail="execution_id or a valid run_id with execution history is required")

    try:
        test_cases = load_saved_test_cases(execution_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    effective_session_id = run_ctx.auth_session_id if run_ctx else None

    # Snapshot the prior execution before execute_test_cases updates the run
    # context. Otherwise the replay compares the new result to itself and
    # always reports zero baseline changes.
    previous_results: dict[str, str] = {}
    if run_ctx and run_ctx.execution_results:
        for r in run_ctx.execution_results:
            key = f"{r.get('TC_ID')}_{r.get('Step_ID')}"
            previous_results[key] = r.get("Status", "")

    try:
        rows, summary, artifacts = await asyncio.to_thread(
            execute_test_cases,
            request.base_url,
            test_cases,
            False,
            None,
            effective_session_id,
            run_id,
        )
    except AuthenticationExpiredError as exc:
        raise HTTPException(
            status_code=401,
            detail="Authentication expired. Refresh the session to continue.",
        ) from exc

    artifacts["regression_of"] = execution_id

    if run_id:
        try:
            from api.db.repository import DBRepository
            replay_id = artifacts.get("execution_id", uuid4().hex)
            DBRepository.record_execution(
                execution_id=replay_id,
                run_id=run_id,
                status="completed",
                rows=[r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rows],
                summary=summary.model_dump(),
            )
            if run_ctx:
                run_ctx.execution_results = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rows]
                run_ctx.execution_summary = summary.model_dump()
                run_ctx.artifact_paths.update(artifacts)
                run_ctx.status = "executed"
                save_run_context(run_ctx)
        except Exception as persist_exc:
            logger.warning("[API] Regression completed but persistence failed for %s: %s", run_id, persist_exc)

    # Compute regression diff
    current_results: dict[str, str] = {}
    for r in rows:
        key = f"{r.TC_ID}_{r.Step_ID}"
        current_results[key] = r.Status

    status_changes = []
    for key, curr_status in current_results.items():
        prev_status = previous_results.get(key)
        if prev_status and prev_status != curr_status:
            status_changes.append({"step": key, "from": prev_status, "to": curr_status})

    diff = {
        "previous_total": len(previous_results),
        "current_total": len(current_results),
        "status_changes": status_changes,
        "newly_passing": sum(1 for sc in status_changes if sc["to"] == "Pass"),
        "newly_failing": sum(1 for sc in status_changes if sc["to"] == "Fail"),
    }

    return RegressionRunResponse(
        status="success",
        run_id=run_id or execution_id,
        execution_id=artifacts.get("execution_id", execution_id),
        summary=summary,
        rows=rows,
        diff=diff,
        artifacts=artifacts,
    )


@router.get(
    "/system-info",
    response_model=SystemInfoResponse,
    summary="Return backend runtime details for the report",
)
async def system_info() -> SystemInfoResponse:
    browser = os.getenv("TEST_BROWSER", os.getenv("BROWSER", "Playwright Chromium"))
    environment = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "local"))
    return SystemInfoResponse(
        os=f"{platform.system()} {platform.release()}".strip(),
        browser=browser,
        python_version=platform.python_version(),
        environment=environment,
    )


def _build_pytest_script(
    base_url: str,
    scan: ScanResponse,
    description: str,
    feature_focus: str,
    test_cases: list[TestCase] | None = None,
) -> str:
    lines = [
        '"""Generated from UI scan intelligence."""',
        "",
        "from urllib.parse import urljoin",
        "",
        "",
        "def test_generated_grounded_regression_cases(page):",
        f"    base_url = {base_url!r}",
    ]
    if description:
        lines.append(f"    test_description = {description!r}")
    if feature_focus:
        lines.append(f"    feature_focus = {feature_focus!r}")
    lines.extend(
        [
            "    discovered_routes = [",
        ]
    )

    for route in scan.routes:
        components = route.components
        targets = (
            len(components.buttons)
            + len(components.inputs)
            + len(components.dropdowns)
            + len(components.tables)
            + len(components.charts)
            + len(components.maps)
        )
        lines.append(
            "        "
            + repr(
                {
                    "path": route.path,
                    "module": route.menu_name or route.path.strip("/") or "Home",
                    "targets": targets,
                }
            )
            + ","
        )

    lines.extend(["    ]", "", "    assert discovered_routes, 'No routes were discovered during scan'"])
    cases = test_cases or []
    if cases:
        for case in cases:
            lines.append(f"\n    # {case.tc_id}: {case.testcase or case.title} [{case.test_type}]")
            for step in case.steps:
                route = step.route or case.route
                selector = (step.selector or {}).get("value", "")
                strategy = (step.selector or {}).get("strategy", "")
                locator_expr = f"page.locator({selector!r})"
                if strategy == "role" or selector.startswith("role="):
                    role_match = re.match(r"role=([^\[]+)(?:\[name=(.*)\])?", selector)
                    if role_match:
                        role_name = role_match.group(1).strip()
                        name = (role_match.group(2) or "").strip("\\\"'")
                        locator_expr = f"page.get_by_role({role_name!r}, name={name!r})" if name else f"page.get_by_role({role_name!r})"
                elif strategy == "text":
                    locator_expr = f"page.get_by_text({selector!r})"
                elif selector.startswith("xpath="):
                    locator_expr = f"page.locator({selector!r})"
                if step.action == "navigate":
                    lines.append(f"    page.goto(urljoin(base_url.rstrip('/') + '/', {route!r}.lstrip('/'))) ")
                elif step.action == "assert_url":
                    lines.append(f"    assert {route!r} in page.url")
                elif step.action == "assert_visible" and selector:
                    lines.append(f"    assert {locator_expr}.first.is_visible()")
                elif step.action == "click" and selector:
                    lines.append(f"    {locator_expr}.first.click()")
                elif step.action == "type" and selector:
                    lines.append(f"    {locator_expr}.first.fill({(step.value or 'test')!r})")
                elif step.action == "select" and selector:
                    lines.append(f"    {locator_expr}.first.select_option({(step.value or 'lohi')!r})")
    else:
        lines.extend([
            "    for route in discovered_routes:",
            "        page.goto(urljoin(base_url.rstrip('/') + '/', route['path'].lstrip('/')))",
            "        assert page.url",
            "        assert route['targets'] > 0, f\"No automation targets found for {route['path']}\"",
        ])
    return "\n".join(lines) + "\n"


def _build_llm_prompt(request: GenerateTestRequest, context: dict, user_prompt: str) -> str:
    """Create a bounded, data-only prompt; never include credentials or runtime events."""
    routes = [
        {
            "path": route.path,
            "module": route.menu_name,
            "purpose": route.purpose,
            "status": route.route_status,
            "score": route.route_score,
            "components": route.components.model_dump(),
        }
        for route in request.scan.routes
    ]
    return "\n".join((
        "You are a test-planning assistant. Produce concise planning notes, not code.",
        "Use only the supplied scan map and user artifacts. Identify module coverage, missing coverage, negative cases, and risk scenarios.",
        f"User prompt: {user_prompt or '(none)'}",
        f"Feature focus: {request.feature_focus or '(none)'}",
        "Requirements artifact:\n" + context.get("requirements", "")[:50_000],
        "Existing testcase artifact:\n" + context.get("testcases", "")[:50_000],
        "Observed UI scan map:\n" + json.dumps(routes, ensure_ascii=False, indent=2),
    ))

# End of router helpers.


@router.get("/run/{run_id}/state", summary="Retrieve active run context and workflow state")
async def get_run_state(run_id: str) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    run_ctx = get_run_context(run_id)
    try:
        latest_checkpoint = DBRepository.get_latest_checkpoint(run_id)
    except Exception:
        latest_checkpoint = None
    latest_event = get_latest_telemetry(run_id) or {}

    checkpoint_state = (latest_checkpoint or {}).get("state_data", {}) if latest_checkpoint else {}
    route_progress = latest_event.get("route_progress") or checkpoint_state.get("route_progress") or {}
    scanned = int(route_progress.get("scanned", len(checkpoint_state.get("visited_routes", []))) or 0)
    pending = int(route_progress.get("pending", len(checkpoint_state.get("pending_routes", []))) or 0)
    total = int(route_progress.get("total", scanned + pending) or 0)

    if not run_ctx:
        checkpoint_file = REPORT_PATH / f"checkpoint_{run_id}.json"
        if checkpoint_file.is_file():
            try:
                chk_data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                return {
                    "run_id": run_id,
                    "scan_id": run_id,
                    "target_origin": chk_data.get("namespace", ""),
                    "allowed_path_namespace": chk_data.get("allowed_scope", "/"),
                    "auth_status": chk_data.get("auth_status", "none"),
                    "status": "partial_scanned",
                    "checkpoints": [run_id],
                    "selected_modules": chk_data.get("selected_modules", chk_data.get("completed_modules", [])),
                    "completed_modules": chk_data.get("completed_modules", []),
                    "pending_modules": [
                        m for m in chk_data.get("selected_modules", chk_data.get("completed_modules", []))
                        if m not in chk_data.get("completed_modules", [])
                    ],
                    "current_module": chk_data.get("current_module_id", ""),
                    "current_route": chk_data.get("current_route", ""),
                    "route_progress": {
                        "scanned": len(chk_data.get("visited_routes", [])),
                        "pending": len(chk_data.get("pending_routes", [])),
                        "total": len(chk_data.get("visited_routes", [])) + len(chk_data.get("pending_routes", [])),
                    },
                    "module_progress": {
                        "current": min(
                            len(chk_data.get("selected_modules", chk_data.get("completed_modules", []))),
                            len(chk_data.get("completed_modules", [])) + 1,
                        ) if chk_data.get("selected_modules", chk_data.get("completed_modules", [])) else 0,
                        "total": len(chk_data.get("selected_modules", chk_data.get("completed_modules", []))),
                    },
                    "completed_module_count": len(chk_data.get("completed_modules", [])),
                    "pending_module_count": max(
                        0,
                        len(chk_data.get("selected_modules", chk_data.get("completed_modules", [])))
                        - len(chk_data.get("completed_modules", [])),
                    ),
                    "elements_found": len(chk_data.get("element_map_snapshot", [])),
                    "safe_actions_explored": len(chk_data.get("scan_events", [])),
                    "parking_lot_actions": len(chk_data.get("parking_lot_actions", [])),
                    "retry_count": int((latest_event or {}).get("retry_count", 0) or 0),
                    "last_event_sequence": int((latest_event or {}).get("last_event_sequence", 0) or 0),
                    "latest_telemetry": latest_event or {},
                    "checkpoint_version": int((latest_checkpoint or {}).get("version", 0) or 0),
                    "scan_results": {
                        "page_intelligence": chk_data.get("page_intelligence", {}),
                        "route_validations": chk_data.get("route_validations", {}),
                        "parking_lot_actions": chk_data.get("parking_lot_actions", []),
                    },
                }
            except Exception:
                pass
        raise HTTPException(status_code=404, detail=f"Run context '{run_id}' not found")
    data = run_ctx.model_dump(mode="json")
    latest_sequence = int(latest_event.get("last_event_sequence", 0) or 0)
    current_sequence = int(data.get("last_event_sequence", 0) or 0)
    event_run = (latest_event.get("run_id") or latest_event.get("scan_id") or "").strip()
    event_scan = (latest_event.get("scan_id") or "").strip()
    same_run = (not event_run or event_run == run_id) and (not event_scan or event_scan == (data.get("scan_id") or run_id))
    use_latest_event = same_run and latest_sequence >= current_sequence
    if use_latest_event and latest_event.get("timestamp") and data.get("updated_at"):
        try:
            run_updated = datetime.fromisoformat(str(data["updated_at"]).replace("Z", "+00:00"))
            event_ts = datetime.fromtimestamp(float(latest_event["timestamp"]), tz=timezone.utc)
            if event_ts < run_updated:
                use_latest_event = False
        except Exception:
            pass
    effective_event = latest_event if use_latest_event else {}

    data["scan_id"] = data.get("scan_id") or run_id
    # HTTP clients can consume this snapshot when WebSocket upgrade/forwarding
    # is unavailable through a tunnel.  Keep the existing state contract and
    # expose only the latest already-published event as a polling fallback.
    data["latest_telemetry"] = effective_event or latest_event or {}
    data["current_module"] = effective_event.get("current_module", data.get("current_module"))
    data["current_route"] = effective_event.get("current_route", data.get("current_route"))
    data["route_progress"] = calculate_route_progress({
        "route_progress": effective_event.get("route_progress") or {"scanned": scanned, "pending": pending, "total": total},
        "scan_results": data.get("scan_results") or {},
    })
    data["elements_found"] = int(effective_event.get("elements_found", data.get("elements_found", 0)) or 0)
    data["safe_actions_explored"] = int(effective_event.get("safe_actions_explored", data.get("safe_actions_explored", 0)) or 0)
    data["parking_lot_actions"] = int(effective_event.get("parking_lot_actions", data.get("parking_lot_actions", 0)) or 0)
    data["retry_count"] = int(effective_event.get("retry_count", data.get("retry_count", 0)) or 0)
    data["last_event_sequence"] = int(effective_event.get("last_event_sequence", data.get("last_event_sequence", 0)) or 0)
    module_progress = calculate_module_progress({
        "selected_modules": data.get("selected_modules") or [],
        "completed_modules": data.get("completed_modules") or [],
    })
    data["module_progress"] = {"current": module_progress["current"], "total": module_progress["total"]}
    data["completed_module_count"] = module_progress["completed_count"]
    data["pending_module_count"] = module_progress["pending_count"]
    data["completed_modules"] = module_progress["completed_modules"]
    data["pending_modules"] = module_progress["pending_modules"]
    if data.get("status") == "scanned" and module_progress["pending_count"] == 0 and int(data["route_progress"]["pending"]) == 0:
        data["current_module"] = ""
        data["current_route"] = ""
    data["checkpoint_version"] = int((latest_checkpoint or {}).get("version", data.get("checkpoint_version", 0)) or 0)
    data["is_paused"] = is_scan_paused(run_id)
    return data


@router.post("/scan/control", response_model=ScanControlResponse, summary="Send live execution control signal to an active scan")
async def scan_control(request: ScanControlRequest) -> ScanControlResponse:
    action = request.action.strip().lower()
    run_id = request.run_id
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    run_ctx = get_run_context(run_id)

    if action == "pause":
        pause_scan(run_id)
        if run_ctx:
            run_ctx.status = "paused"
            save_run_context(run_ctx)
        msg = f"Pause signal sent to scan run '{run_id}'."
    elif action == "resume":
        resume_scan(run_id)
        if run_ctx and run_ctx.status in {"paused", "running"}:
            run_ctx.status = "running"
            save_run_context(run_ctx)
        msg = f"Resume signal sent to scan run '{run_id}'."
    elif action in {"continue", "retry"}:
        set_user_decision(run_id, action)
        if run_ctx and run_ctx.status in {"paused", "running"}:
            run_ctx.status = "running"
            save_run_context(run_ctx)
        msg = f"Decision '{action}' registered for scan run '{run_id}'."
    elif action in {"end", "stop"}:
        set_user_decision(run_id, "end")
        stop_scan(run_id)
        if run_ctx:
            run_ctx.status = "partial_scanned"
            save_run_context(run_ctx)
        msg = f"End-and-save signal sent to scan run '{run_id}'."
    elif action == "save":
        if run_ctx:
            save_run_context(run_ctx)
        msg = f"Save progress signal processed for scan run '{run_id}'."
    elif action == "discard":
        stop_scan(run_id)
        run_ctx = get_run_context(run_id)
        if run_ctx:
            run_ctx.status = "discarded"
            save_run_context(run_ctx)
        msg = f"Discard signal processed for scan run '{run_id}'."
    else:
        raise HTTPException(status_code=400, detail=f"Invalid scan control action: '{request.action}'")
    publish(run_id, {"type": f"scan_control_{action}", "run_id": run_id, "scan_id": run_id, "action": action, "message": msg})
    return ScanControlResponse(status="success", run_id=run_id, action=action, message=msg)


@router.get("/reports/{run_id}/status", response_model=ArtifactStatusResponse, summary="Check status and availability of scan/execution artifacts")
async def reports_status(run_id: str) -> ArtifactStatusResponse:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    run_ctx = get_run_context(run_id)
    base_stem = f"{run_id}_crawl_results"

    json_path = REPORT_PATH / f"{base_stem}.json"
    yaml_path = REPORT_PATH / f"{base_stem}.yaml"
    md_path = REPORT_PATH / f"{base_stem}_summary.md"
    excel_path = REPORT_PATH / f"{base_stem}.xlsx"
    if not excel_path.is_file() and (REPORT_PATH / run_id / "execution_results.xlsx").is_file():
        excel_path = REPORT_PATH / run_id / "execution_results.xlsx"
    chk_path = REPORT_PATH / f"checkpoint_{run_id}.json"

    exec_dir = REPORT_PATH / run_id
    exec_json = (exec_dir / "execution_results.json") if exec_dir.exists() else None

    screenshots = []
    if exec_dir.exists():
        screenshots.extend(list(exec_dir.glob("*.png")))
    screenshots.extend(list(REPORT_PATH.glob(f"{run_id}*.png")))
    log_path = LOG_PATH / f"{run_id}.log"

    artifacts_ready = {
        "json": json_path.is_file(),
        "yaml": yaml_path.is_file(),
        "markdown": md_path.is_file(),
        "excel": excel_path.is_file(),
        "execution_results": bool(exec_json and exec_json.is_file()) or bool(run_ctx and run_ctx.execution_results),
        "checkpoint": chk_path.is_file(),
        "screenshots": len(screenshots) > 0,
        "log": log_path.is_file(),
    }

    download_urls = {
        k: f"/reports/{run_id}/download/{k}" for k, v in artifacts_ready.items() if v
    }

    is_in_progress = bool(run_ctx and run_ctx.status in {"running", "paused", "executing"})
    status_str = "in_progress" if is_in_progress else ("ready" if any(artifacts_ready.values()) else "not_found")

    return ArtifactStatusResponse(
        run_id=run_id,
        status=status_str,
        artifacts=artifacts_ready,
        download_urls=download_urls,
    )


@router.api_route("/reports/{run_id}/download/{artifact_type}", methods=["GET", "HEAD"], summary="Download specific artifact by stable run ID URL")
async def download_artifact(run_id: str, artifact_type: str):
    from fastapi.responses import FileResponse, Response
    import zipfile
    import io

    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")

    if artifact_type not in ALLOWED_ARTIFACT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported artifact type: '{artifact_type}'")

    if "storage_state" in artifact_type.lower() or "auth_sessions" in artifact_type.lower():
        raise HTTPException(status_code=403, detail="Access denied: Forbidden artifact access")

    run_ctx = get_run_context(run_id)
    display_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", (run_ctx.display_name if run_ctx else "") or run_id).strip("._-")[:80] or run_id
    is_in_progress = bool(run_ctx and run_ctx.status in {"running", "paused", "executing"})
    base_stem = f"{run_id}_crawl_results"

    media_types = {
        "json": "application/json",
        "yaml": "application/x-yaml",
        "markdown": "text/markdown",
        "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "execution_results": "application/json",
        "checkpoint": "application/json",
        "screenshots": "application/zip",
        "log": "text/plain",
    }

    target_file: Path | None = None
    filename: str = ""

    if artifact_type == "json":
        target_file = REPORT_PATH / f"{base_stem}.json"
        filename = f"{display_stem}_crawl_results.json"
    elif artifact_type == "yaml":
        target_file = REPORT_PATH / f"{base_stem}.yaml"
        filename = f"{display_stem}_crawl_results.yaml"
    elif artifact_type == "markdown":
        target_file = REPORT_PATH / f"{base_stem}_summary.md"
        filename = f"{display_stem}_crawl_results_summary.md"
    elif artifact_type == "excel":
        target_file = REPORT_PATH / f"{base_stem}.xlsx"
        filename = f"{display_stem}_crawl_results.xlsx"
        if not target_file.is_file():
            execution_excel = REPORT_PATH / run_id / "execution_results.xlsx"
            if execution_excel.is_file():
                target_file = execution_excel
                filename = f"{display_stem}_execution_results.xlsx"
    elif artifact_type == "checkpoint":
        target_file = REPORT_PATH / f"checkpoint_{run_id}.json"
        filename = f"{display_stem}_checkpoint.json"
    elif artifact_type == "execution_results":
        exec_dir = REPORT_PATH / run_id
        if exec_dir.exists() and (exec_dir / "execution_results.json").is_file():
            target_file = exec_dir / "execution_results.json"
            filename = f"{display_stem}_execution_results.json"
        elif run_ctx and run_ctx.artifact_paths and "json" in run_ctx.artifact_paths:
            candidate = Path(run_ctx.artifact_paths["json"])
            if candidate.is_file():
                target_file = candidate
                filename = f"{display_stem}_execution_results.json"
        if not target_file and run_ctx and run_ctx.execution_results:
            safe_results = redact_sensitive_data(run_ctx.execution_results)
            safe_summary = redact_sensitive_data(run_ctx.execution_summary or {})
            content = json.dumps({"summary": safe_summary, "rows": safe_results}, indent=2)
            return Response(
                content=content,
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{display_stem}_execution_results.json"'},
            )
    elif artifact_type == "screenshots":
        exec_dir = REPORT_PATH / run_id
        images = []
        if exec_dir.exists():
            images.extend(list(exec_dir.glob("*.png")))
        images.extend(list(REPORT_PATH.glob(f"{run_id}*.png")))
        if run_ctx and run_ctx.artifact_paths:
            for p in run_ctx.artifact_paths.values():
                try:
                    pth = Path(p)
                    if pth.is_dir():
                        images.extend(list(pth.glob("*.png")))
                    elif pth.suffix.lower() == ".png" and pth.is_file():
                        images.append(pth)
                except Exception:
                    pass

        MAX_SCREENSHOT_COUNT = 500
        MAX_SCREENSHOT_BYTES = 50 * 1024 * 1024
        selected = images[:MAX_SCREENSHOT_COUNT]
        total_size = sum(img.stat().st_size for img in selected if img.is_file())
        if total_size > MAX_SCREENSHOT_BYTES:
            raise HTTPException(status_code=413, detail="Screenshot package exceeds maximum allowed size (50 MB)")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if selected:
                for img in selected:
                    resolved_img = img.resolve()
                    if resolved_img.is_relative_to(REPORT_PATH.resolve()):
                        zf.write(resolved_img, arcname=img.name)
            else:
                zf.writestr(f"{run_id}_info.txt", f"No failure screenshots generated for run {run_id}.\n")
        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{display_stem}_screenshots.zip"'},
        )
    elif artifact_type == "log":
        target_file = LOG_PATH / f"{run_id}.log"
        filename = f"{display_stem}_failure.log"

    if target_file and target_file.is_file():
        resolved = target_file.resolve()
        report_root = REPORT_PATH.resolve()
        log_root = LOG_PATH.resolve()
        if not (resolved.is_relative_to(report_root) or resolved.is_relative_to(log_root)):
            raise HTTPException(status_code=403, detail="Access denied: Path traversal detected")
        is_owned_artifact = (
            resolved.name.startswith(run_id)
            or resolved.name == f"checkpoint_{run_id}.json"
            or resolved.parent.name == run_id
            or (artifact_type == "log" and resolved == (LOG_PATH / f"{run_id}.log").resolve())
            or (run_ctx and str(resolved) in [str(Path(p).resolve()) for p in run_ctx.artifact_paths.values()])
        )
        if not is_owned_artifact:
            raise HTTPException(status_code=403, detail=f"Access denied: Artifact does not belong to run '{run_id}'")

        return FileResponse(
            str(resolved),
            media_type=media_types.get(artifact_type, "application/octet-stream"),
            filename=filename or target_file.name,
        )

    if is_in_progress:
        raise HTTPException(
            status_code=409,
            detail=f"Artifact '{artifact_type}' for run '{run_id}' is generating or scan/execution is still in progress."
        )
    raise HTTPException(
        status_code=404,
        detail=f"Artifact '{artifact_type}' not found for run '{run_id}'."
    )


@router.api_route("/reports/{run_id}/screenshot/{filename}", methods=["GET", "HEAD"], summary="Download specific screenshot safely")
async def download_screenshot(run_id: str, filename: str):
    from fastapi.responses import FileResponse

    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")

    if not filename or not re.fullmatch(r"^[A-Za-z0-9_.-]+\.png$", filename):
        raise HTTPException(status_code=400, detail="Invalid screenshot filename format")

    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Path traversal detected")

    exec_dir = REPORT_PATH / run_id
    cand1 = exec_dir / filename
    cand2 = REPORT_PATH / filename

    target = cand1 if cand1.is_file() else (cand2 if cand2.is_file() else None)
    if not target:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    resolved = target.resolve()
    base_resolved = REPORT_PATH.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise HTTPException(status_code=403, detail="Access denied: Path traversal detected")

    if not (resolved.name.startswith(run_id) or (exec_dir.exists() and resolved.is_relative_to(exec_dir.resolve()))):
        raise HTTPException(status_code=403, detail="Access denied: Screenshot does not belong to this run")

    # This endpoint is used as the report modal's <img src>.  FileResponse's
    # default `attachment` disposition makes browsers download the PNG instead
    # of rendering it inline.
    return FileResponse(
        str(resolved),
        media_type="image/png",
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ===========================================================================
# Persistence, Resumable Runs, and Regression Suite Management Endpoints
# ===========================================================================

@router.get("/system/db-status", summary="Check database connectivity and configuration status")
async def system_db_status() -> dict[str, Any]:
    return check_db_status()


@router.get("/runs", summary="List saved runs with progress and status")
async def list_runs_endpoint(
    base_url: str | None = None,
    status: str | None = None,
    is_archived: bool = False,
    skip: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return list_saved_runs(base_url=base_url, is_archived=is_archived, skip=skip, limit=limit)


@router.post("/runs", summary="Create a new scan run context")
async def create_run_endpoint(request: CreateRunRequest) -> dict[str, Any]:
    run_id = uuid4().hex
    ctx = create_run_context(
        target_origin=request.url,
        allowed_path_namespace=request.allowed_scope or "/",
        run_id=run_id,
        username=request.username,
        url=request.url,
    )
    if request.selected_module_ids:
        ctx.selected_modules = request.selected_module_ids
        save_run_context(ctx)
    return {
        "status": "created",
        "run_id": ctx.run_id,
        "base_url": ctx.target_origin,
        "allowed_scope": ctx.allowed_path_namespace,
    }


@router.get("/runs/{run_id}", summary="Get run details including modules, routes, and checkpoints")
async def get_run_endpoint(run_id: str) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    run_ctx = get_run_context(run_id)
    if run_ctx:
        graph = run_ctx.route_graph if isinstance(run_ctx.route_graph, dict) else {}
        graph_nodes = graph.get("nodes")
        route_count = len(graph_nodes) if isinstance(graph_nodes, list) else len(graph)
        return {
            "run_id": run_ctx.run_id,
            "display_name": run_ctx.display_name or run_ctx.run_id,
            "application_url": run_ctx.target_origin,
            "allowed_scope": run_ctx.allowed_path_namespace,
            "status": run_ctx.status,
            "auth_status": run_ctx.auth_status,
            "username": run_ctx.username,
            "progress_percentage": 100.0 if run_ctx.status in {"scanned", "executed"} else 50.0,
            "selected_modules": list(run_ctx.selected_modules),
            "completed_modules": list(run_ctx.completed_modules),
            "pending_modules": list(run_ctx.pending_modules),
            "discovered_modules": run_ctx.discovered_modules,
            "routes": (
                [{"path": n} for n in graph_nodes]
                if isinstance(graph_nodes, list)
                else [{"path": path, **(node if isinstance(node, dict) else {})} for path, node in graph.items()]
            ),
            "route_count": route_count,
            "execution_summary": run_ctx.execution_summary or {},
            "created_at": run_ctx.created_at,
            "updated_at": run_ctx.updated_at,
        }
    run_data = DBRepository.get_run(run_id)
    if run_data:
        return run_data
    raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")


@router.post("/runs/{run_id}/resume", summary="Resume an interrupted or paused scan run")
async def resume_run_endpoint(run_id: str, request: ResumeRunRequest) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    run_ctx = get_run_context(run_id)
    if not run_ctx:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    from automation_framework.storage.checkpoint_store import compute_namespace, load_checkpoint
    chk = load_checkpoint(run_id)
    if chk and chk.get("namespace"):
        expected_ns = compute_namespace(run_ctx.target_origin)
        if chk["namespace"] != expected_ns:
            raise HTTPException(
                status_code=400,
                detail="Checkpoint namespace mismatch: cannot resume run across differing origins or environments"
            )

    if run_ctx.auth_session_id:
        valid = check_auth_session_validity(run_ctx.auth_session_id)
        if not valid and not (request.username and request.password):
            return {
                "status": "auth_required",
                "run_id": run_id,
                "message": "Authentication session expired. Explicit credentials or re-login required to resume."
            }

    resume_scan(run_id)
    run_ctx.status = "running"
    save_run_context(run_ctx)

    return {
        "status": "resumed",
        "run_id": run_id,
        "current_module": chk.get("current_module_id") if chk else None,
        "current_route": chk.get("current_route") if chk else None,
        "completed_routes": chk.get("completed_routes", []) if chk else [],
        "message": f"Run '{run_id}' resumed successfully."
    }


@router.post("/runs/{run_id}/pause", summary="Pause an active scan run")
async def pause_run_endpoint(run_id: str) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    pause_scan(run_id)
    run_ctx = get_run_context(run_id)
    if run_ctx:
        run_ctx.status = "paused"
        save_run_context(run_ctx)
    return {"status": "paused", "run_id": run_id, "message": f"Run '{run_id}' paused."}


@router.post("/runs/{run_id}/save-progress", summary="Explicitly save current progress and checkpoint")
async def save_progress_endpoint(run_id: str, request: SaveProgressRequest) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    run_ctx = get_run_context(run_id)
    if not run_ctx:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    from automation_framework.storage.checkpoint_store import save_checkpoint, load_checkpoint
    curr_state = request.state or load_checkpoint(run_id) or {}
    curr_state["run_id"] = run_id
    curr_state["base_url"] = run_ctx.target_origin
    curr_state["schema_version"] = "2.0"
    save_checkpoint(run_id, curr_state)
    save_run_context(run_ctx)
    return {"status": "saved", "run_id": run_id, "message": f"Progress for run '{run_id}' saved successfully."}


@router.post("/runs/{run_id}/end-and-save", summary="End scanning and save current state")
async def end_and_save_endpoint(run_id: str) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    set_user_decision(run_id, "end")
    stop_scan(run_id)
    run_ctx = get_run_context(run_id)
    if run_ctx:
        run_ctx.status = "partial_scanned"
        save_run_context(run_ctx)
    publish(run_id, {"type": "scan_stopped", "run_id": run_id, "scan_id": run_id, "scan_stopped_early": True, "status": "partial_success"})
    return {"status": "partial_scanned", "run_id": run_id, "message": f"Run '{run_id}' is stopping and saving a partial checkpoint."}


@router.post("/runs/{run_id}/discard", summary="Discard run and cancel active tasks")
async def discard_run_endpoint(run_id: str) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    stop_scan(run_id)
    run_ctx = get_run_context(run_id)
    if run_ctx:
        run_ctx.status = "discarded"
        save_run_context(run_ctx)
    return {"status": "discarded", "run_id": run_id, "message": f"Run '{run_id}' discarded."}


@router.post("/runs/{run_id}/duplicate", summary="Duplicate a saved run")
async def duplicate_run_endpoint(run_id: str) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    dup = DBRepository.duplicate_run(run_id)
    if not dup:
        run_ctx = get_run_context(run_id)
        if not run_ctx:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        new_id = uuid4().hex
        new_ctx = create_run_context(
            target_origin=run_ctx.target_origin,
            allowed_path_namespace=run_ctx.allowed_path_namespace,
            run_id=new_id,
            username=run_ctx.username,
        )
        new_ctx.selected_modules = list(run_ctx.selected_modules)
        new_ctx.discovered_modules = list(run_ctx.discovered_modules)
        save_run_context(new_ctx)
        return {"status": "duplicated", "source_run_id": run_id, "new_run_id": new_id}
    return {"status": "duplicated", "source_run_id": run_id, "new_run_id": dup.get("run_id")}


@router.post("/runs/{run_id}/archive", summary="Archive a saved run")
async def archive_run_endpoint(run_id: str) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    DBRepository.archive_run(run_id)
    run_ctx = get_run_context(run_id)
    if run_ctx:
        run_ctx.is_archived = True
        save_run_context(run_ctx)
    return {"status": "archived", "run_id": run_id}


@router.delete("/runs/{run_id}", summary="Delete a saved run")
async def delete_run_endpoint(run_id: str) -> dict[str, Any]:
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")
    DBRepository.delete_run(run_id)
    delete_run_context(run_id)
    return {"status": "deleted", "run_id": run_id}


# --- Regression Suite Endpoints ---

@router.post("/regression/suites", summary="Create a named regression suite")
async def create_regression_suite_endpoint(request: CreateRegressionSuiteRequest) -> dict[str, Any]:
    case_ids = list(request.test_case_ids or [])
    if request.baseline_run_id and not case_ids:
        # A baseline run is the user's selection of test cases. Populate the
        # suite automatically instead of creating an empty 0/0 suite.
        from api.db.models import TestCaseModel
        from api.db.session import get_db_session
        with get_db_session() as session:
            # A run may have older generated-plan rows from before the plan
            # was made idempotent. Select one current case per stable tc_id.
            seen_ids: set[str] = set()
            for case in session.query(TestCaseModel).filter(
                TestCaseModel.run_id == request.baseline_run_id
            ).order_by(TestCaseModel.created_at.desc()).all():
                if case.tc_id not in seen_ids:
                    case_ids.append(case.id)
                    seen_ids.add(case.tc_id)
    return DBRepository.create_regression_suite(
        name=request.name,
        base_url=request.base_url,
        environment=request.environment,
        description=request.description,
        baseline_run_id=request.baseline_run_id,
        test_case_ids=case_ids,
        filter_tags=request.filter_tags,
        filter_modules=request.filter_modules,
    )


@router.get("/regression/suites", summary="List all regression suites")
async def list_regression_suites_endpoint(base_url: str | None = None, is_archived: bool = False) -> list[dict[str, Any]]:
    return DBRepository.list_regression_suites(base_url=base_url, is_archived=is_archived)


@router.get("/regression/suites/{suite_id}", summary="Get regression suite details and test cases")
async def get_regression_suite_endpoint(suite_id: str) -> dict[str, Any]:
    suite = DBRepository.get_regression_suite(suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail=f"Regression suite '{suite_id}' not found")
    return suite


@router.put("/regression/suites/{suite_id}", summary="Update a regression suite")
async def update_regression_suite_endpoint(suite_id: str, request: UpdateRegressionSuiteRequest) -> dict[str, Any]:
    updated = DBRepository.update_regression_suite(
        suite_id=suite_id,
        name=request.name,
        description=request.description,
        environment=request.environment,
        case_toggles=request.case_toggles,
        filter_tags=request.filter_tags,
        filter_modules=request.filter_modules,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Regression suite '{suite_id}' not found")
    return updated


@router.post("/regression/suites/{suite_id}/duplicate", summary="Duplicate a regression suite")
async def duplicate_regression_suite_endpoint(suite_id: str) -> dict[str, Any]:
    dup = DBRepository.duplicate_regression_suite(suite_id)
    if not dup:
        raise HTTPException(status_code=404, detail=f"Regression suite '{suite_id}' not found")
    return dup


@router.post("/regression/suites/{suite_id}/archive", summary="Archive a regression suite")
async def archive_regression_suite_endpoint(suite_id: str) -> dict[str, Any]:
    ok = DBRepository.archive_regression_suite(suite_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Regression suite '{suite_id}' not found")
    return {"status": "archived", "suite_id": suite_id}


@router.delete("/regression/suites/{suite_id}", summary="Delete a regression suite")
async def delete_regression_suite_endpoint(suite_id: str) -> dict[str, Any]:
    ok = DBRepository.delete_regression_suite(suite_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Regression suite '{suite_id}' not found")
    return {"status": "deleted", "suite_id": suite_id}


@router.delete("/regression/suites", summary="Delete all regression suites")
async def delete_all_regression_suites_endpoint() -> dict[str, Any]:
    count = DBRepository.delete_all_regression_suites()
    return {"status": "deleted", "deleted_count": count}


@router.delete("/artifacts", summary="Delete all generated artifacts")
async def delete_all_artifacts_endpoint() -> dict[str, Any]:
    """Remove generated reports while preserving the database and auth state."""
    from api.services.security_scan_service import clear_reports
    try:
        clear_reports()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    deleted_files = 0
    report_root = Path(REPORT_PATH)
    preserved = {"pione_testmate.db", "auth_sessions", "run_contexts"}
    if report_root.exists():
        for child in report_root.iterdir():
            if child.name in preserved:
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                deleted_files += 1
            except OSError as exc:
                logger.warning(f"[ARTIFACTS] Could not remove {child}: {exc}")
    deleted_records = DBRepository.delete_all_artifact_records()
    return {
        "status": "deleted",
        "deleted_files": deleted_files,
        "deleted_records": deleted_records,
    }


@router.get("/artifacts", summary="List runs with available generated artifacts")
async def list_artifacts_endpoint() -> list[dict[str, Any]]:
    """Return only saved runs that still have at least one downloadable artifact."""
    artifact_runs: list[dict[str, Any]] = []
    report_root = Path(REPORT_PATH)
    for run in DBRepository.list_runs():
        run_id = str(run.get("run_id", ""))
        if not run_id:
            continue
        has_artifact = (
            any(report_root.glob(f"{run_id}*"))
            or any(report_root.glob(f"checkpoint_{run_id}*"))
            or (report_root / run_id).exists()
        )
        if has_artifact:
            artifact_runs.append(run)
    return artifact_runs


@router.post("/regression/execute-selected", summary="Execute strictly selected regression test cases with confirmation")
async def execute_selected_regression(request: ExecuteSelectedRegressionRequest) -> dict[str, Any]:
    if not request.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Execution rejected: explicit user confirmation is required (confirmed=true).",
        )

    run_id = request.run_id
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")

    run_ctx = get_run_context(run_id)

    candidate_cases: list[TestCase] = []
    if request.suite_id:
        suite = DBRepository.get_regression_suite(request.suite_id)
        if not suite:
            raise HTTPException(status_code=404, detail=f"Suite '{request.suite_id}' not found")
        case_ids = [c["id"] for c in suite.get("cases", []) if c.get("is_enabled")]
        from api.db.models import TestCaseModel
        from api.db.session import get_db_session
        with get_db_session() as session:
            db_cases = session.query(TestCaseModel).filter(TestCaseModel.id.in_(case_ids)).all()
            for dbc in db_cases:
                candidate_cases.append(TestCase(
                    tc_id=dbc.tc_id,
                    title=dbc.title,
                    module=dbc.module,
                    route=dbc.route,
                    tags=dbc.tags or [],
                    priority=dbc.priority or "Medium",
                    prerequisites=dbc.prerequisites or [],
                    locators=dbc.locators or {},
                    steps=[TestStep(**st) for st in (dbc.steps or [])],
                    safety_classification=dbc.safety_classification or "safe_action",
                    latest_result=dbc.latest_result,
                ))
    elif run_ctx and run_ctx.generated_test_plan:
        candidate_cases = [
            tc if isinstance(tc, TestCase) else TestCase(**tc)
            for tc in run_ctx.generated_test_plan
        ]
    else:
        from api.db.models import TestCaseModel
        from api.db.session import get_db_session
        with get_db_session() as session:
            db_cases = session.query(TestCaseModel).filter(TestCaseModel.run_id == run_id).all()
            for dbc in db_cases:
                candidate_cases.append(TestCase(
                    tc_id=dbc.tc_id,
                    title=dbc.title,
                    module=dbc.module,
                    route=dbc.route,
                    tags=dbc.tags or [],
                    priority=dbc.priority or "Medium",
                    prerequisites=dbc.prerequisites or [],
                    locators=dbc.locators or {},
                    steps=[TestStep(**st) for st in (dbc.steps or [])],
                    safety_classification=dbc.safety_classification or "safe_action",
                    latest_result=dbc.latest_result,
                ))

    if not candidate_cases:
        try:
            candidate_cases = load_saved_test_cases(run_id)
        except Exception:
            pass

    if not candidate_cases:
        raise HTTPException(status_code=404, detail=f"No test cases found for run '{run_id}'. Generate tests first.")

    selected_cases: list[TestCase] = []
    req_ids = set(request.selected_test_case_ids)
    req_modules = set(request.selected_modules)
    req_routes = set(request.selected_routes)
    req_tags = set(request.selected_tags)

    for tc in candidate_cases:
        if req_ids and tc.tc_id not in req_ids:
            continue
        if req_modules and tc.module not in req_modules:
            continue
        if req_routes and tc.route not in req_routes:
            continue
        if req_tags and not any(tag in req_tags for tag in (tc.tags or [])):
            continue
        if (request.failed_only or request.filter_type == "failed_only") and tc.latest_result != "Fail":
            continue
        if (request.never_executed_only or request.filter_type == "never_executed") and tc.latest_result:
            continue
        if (request.manual_review_only or request.filter_type == "review_required") and tc.safety_classification != "parking_lot":
            continue

        selected_cases.append(tc)

    if not selected_cases:
        raise HTTPException(
            status_code=400,
            detail="No test cases match the selection criteria. Zero tests executed.",
        )

    effective_session_id = request.auth_session_id or (run_ctx.auth_session_id if run_ctx else None)

    try:
        rows, summary, artifacts = await asyncio.to_thread(
            execute_test_cases,
            request.base_url,
            selected_cases,
            request.allow_mutating_actions,
            None,
            effective_session_id,
            run_id,
        )
    except AuthenticationExpiredError as exc:
        raise HTTPException(
            status_code=401,
            detail="Authentication expired. Refresh session to continue.",
        ) from exc

    exec_id = artifacts.get("execution_id", uuid4().hex)
    try:
        DBRepository.record_execution(
            execution_id=exec_id,
            run_id=run_id,
            suite_id=request.suite_id,
            status="completed",
            rows=[r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rows],
            summary=summary,
        )
    except Exception as exc:
        logger.debug("[DB] record_execution note: %s", exc)

    if run_ctx:
        run_ctx.execution_results = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rows]
        run_ctx.status = "executed"
        save_run_context(run_ctx)

    return {
        "status": "success",
        "run_id": run_id,
        "execution_id": exec_id,
        "selected_test_count": len(selected_cases),
        "executed_test_count": len(selected_cases),
        "total_steps": len(rows),
        "summary": summary,
        "rows": rows,
        "artifacts": artifacts,
    }


async def _run_selected_execution_job(job_id: str, request: ExecuteSelectedRegressionRequest) -> None:
    try:
        result = await execute_selected_regression(request)
        _BACKGROUND_EXECUTION_JOBS[job_id] = {
            "status": "completed",
            "result": jsonable_encoder(result),
        }
        _persist_execution_job_state(
            request.run_id, job_id, "completed",
            selected_count=len(request.selected_test_case_ids), result=result,
        )
    except HTTPException as exc:
        _BACKGROUND_EXECUTION_JOBS[job_id] = {
            "status": "failed",
            "error": exc.detail,
            "http_status": exc.status_code,
        }
        _persist_execution_job_state(request.run_id, job_id, "failed", error=str(exc.detail))
    except Exception as exc:
        logger.exception("Background selective execution failed for %s", job_id)
        _BACKGROUND_EXECUTION_JOBS[job_id] = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "http_status": 500,
        }
        _persist_execution_job_state(request.run_id, job_id, "failed", error=f"{type(exc).__name__}: {exc}")


@router.post("/regression/execute-selected/start", summary="Start selected execution without holding the HTTP request open")
async def start_selected_execution_async(request: ExecuteSelectedRegressionRequest) -> dict[str, str]:
    job_id = uuid4().hex
    _BACKGROUND_EXECUTION_JOBS[job_id] = {"status": "running"}
    _persist_execution_job_state(
        request.run_id, job_id, "running", selected_count=len(request.selected_test_case_ids),
    )
    task = asyncio.create_task(_run_selected_execution_job(job_id, request))
    task.add_done_callback(lambda _: None)
    return {"job_id": job_id, "status": "started"}


@router.get("/regression/execute-selected/status/{job_id}", summary="Get selected execution status")
async def get_selected_execution_status(job_id: str) -> dict[str, Any]:
    if not _RUN_ID_REGEX.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Invalid execution job ID")
    job = _BACKGROUND_EXECUTION_JOBS.get(job_id)
    if not job:
        recovered = _recover_execution_job(job_id)
        if recovered:
            return recovered
        raise HTTPException(status_code=404, detail="Execution job not found")
    return job


# --- Baseline Endpoints ---

@router.post("/baselines/save", summary="Save scan and test plan as reusable baseline")
async def save_baseline_endpoint(request: SaveBaselineRequest) -> dict[str, Any]:
    run_id = request.run_id
    if not run_id or not _RUN_ID_REGEX.fullmatch(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run ID format")

    run_ctx = get_run_context(run_id)
    raw_cases = []
    if run_ctx and run_ctx.generated_test_plan:
        raw_cases = [tc.model_dump() if hasattr(tc, "model_dump") else dict(tc) for tc in run_ctx.generated_test_plan]

    try:
        res = DBRepository.save_baseline(
            run_id=run_id,
            name=request.name,
            version=request.version,
            overwrite=request.confirmed_overwrite,
            test_cases=raw_cases,
        )
        return {"status": "saved", "baseline": res}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/baselines", summary="List saved baselines")
async def list_baselines_endpoint(base_url: str | None = None) -> list[dict[str, Any]]:
    return DBRepository.list_baselines(base_url=base_url)


@router.post("/baselines/compare", summary="Compare current scan/execution against baseline")
async def compare_baseline_endpoint(request: CompareBaselineRequest) -> dict[str, Any]:
    return DBRepository.compare_baseline(
        current_run_id=request.current_run_id,
        baseline_run_id=request.baseline_run_id,
    )

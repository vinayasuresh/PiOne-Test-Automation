"""POST /scan router — the main API endpoint."""

import asyncio
import json
import os
import platform
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

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
    TestContextResponse,
    TestCase,
    TestStep,
)
from api.services import crawler_service, mock_service
from api.services.auth_lifecycle_service import (
    AuthenticationExpiredError,
    check_auth_session_validity,
    refresh_auth_session,
)
from api.services.llm_provider_gateway import LLMProviderGateway
from api.services.llm_service import provider_catalog, request_plan, test_provider_connection
from api.services.run_context_service import (
    get_run_context,
    save_run_context,
)
from api.services.scan_event_bus import get_channel, publish
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

router = APIRouter()


@router.post(
    "/discover-modules",
    response_model=ModuleDiscoveryResponse,
    summary="Authenticate and list modules before scanning",
)
async def discover_modules(request: ModuleDiscoveryRequest) -> ModuleDiscoveryResponse:
    """Return the live application's current menu modules without crawling them."""
    try:
        result = await asyncio.to_thread(
            crawler_service.discover_modules, request.url, request.username, request.password,
        )
        message = (
            "Choose one or more modules to scan. Only those modules and their "
            "safe in-module navigation will be explored."
        )
        return ModuleDiscoveryResponse(**result, message=message)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/session/refresh",
    response_model=RefreshSessionResponse,
    summary="Refresh an expired authentication session in-place",
)
async def refresh_session(request: RefreshSessionRequest) -> RefreshSessionResponse:
    try:
        result = await asyncio.to_thread(
            refresh_auth_session,
            url=request.url,
            username=request.username,
            password=request.password,
            auth_session_id=None,
            run_id=request.run_id,
        )
        return RefreshSessionResponse(
            status="success",
            run_id=result["run_id"],
            auth_session_id=result["auth_session_id"],
            message="Session refreshed successfully. You can continue scanning or execution.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=f"Authentication failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to refresh session: {exc}") from exc


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
        return mock_service.get_mock_response(request.url)

    scan_id = request.scan_id or uuid4().hex
    get_channel(scan_id)
    publish(scan_id, {"type": "scan_started", "scan_id": scan_id, "url": request.url})

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
            request.auth_session_id,
            request.run_id,
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
    plan_result = await asyncio.to_thread(
        gateway.plan_tests,
        request.provider,
        request.model,
        scan_dict,
        requirements_list,
        testcases_list,
        combined_prompt,
    )

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
                )
                for s in rc.get("steps", [])
            ]
            test_cases.append(TestCase(
                tc_id=rc.get("tc_id", ""),
                title=rc.get("title", ""),
                module=rc.get("module", ""),
                route=rc.get("route", ""),
                purpose=rc.get("purpose", ""),
                requirement_references=rc.get("requirement_references", []),
                steps=steps,
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
    )

    metadata = plan_result.get("planning_metadata", {})
    run_id = request.run_id or request.scan.run_id
    if run_id:
        try:
            run_ctx = get_run_context(run_id)
            if run_ctx:
                run_ctx.generated_test_plan = [tc.model_dump() for tc in test_cases]
                run_ctx.generated_script = script
                run_ctx.status = "planned"
                save_run_context(run_ctx)
        except Exception:
            pass

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
    )


@router.get("/llm/providers", response_model=list[LLMProviderInfo], summary="List configured test-planning providers")
async def list_llm_providers() -> list[LLMProviderInfo]:
    return [LLMProviderInfo(**item) for item in provider_catalog()]


@router.post("/llm/test-connection", summary="Test connection to an AI provider")
async def test_llm_connection(provider: str) -> dict:
    return await asyncio.to_thread(test_provider_connection, provider)


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
        return ExecuteTestResponse(run_id=run_id, rows=rows, summary=summary, artifacts=artifacts)
    except AuthenticationExpiredError as exc:
        logger.warning(f"[API] Execution blocked by expired auth: {exc}")
        raise HTTPException(
            status_code=401,
            detail="Authentication expired. Refresh the session to continue.",
        ) from exc


@router.post("/regression-run", response_model=RegressionRunResponse, summary="Rerun a saved controlled test plan")
async def regression_run(request: RegressionRunRequest) -> RegressionRunResponse:
    execution_id = request.execution_id
    run_id = request.run_id
    run_ctx = get_run_context(run_id) if run_id else None

    if not execution_id and run_ctx and run_ctx.artifact_paths.get("execution_id"):
        execution_id = run_ctx.artifact_paths["execution_id"]
    if not execution_id and run_ctx and run_ctx.artifact_paths.get("directory"):
        execution_id = Path(run_ctx.artifact_paths["directory"]).name

    if not execution_id:
        raise HTTPException(status_code=400, detail="execution_id or a valid run_id with execution history is required")

    try:
        test_cases = load_saved_test_cases(execution_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    effective_session_id = run_ctx.auth_session_id if run_ctx else None

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

    # Compute regression diff
    previous_results: dict[str, str] = {}
    if run_ctx and run_ctx.execution_results:
        for r in run_ctx.execution_results:
            key = f"{r.get('TC_ID')}_{r.get('Step_ID')}"
            previous_results[key] = r.get("Status", "")

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
) -> str:
    lines = [
        '"""Generated from UI scan intelligence."""',
        "",
        "from urllib.parse import urljoin",
        "",
        "",
        "def test_discovered_routes_have_automation_targets(page):",
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

    lines.extend(
        [
            "    ]",
            "",
            "    assert discovered_routes, 'No routes were discovered during scan'",
            "    for route in discovered_routes:",
            "        page.goto(urljoin(base_url.rstrip('/') + '/', route['path'].lstrip('/')))",
            "        assert page.url",
            "        assert route['targets'] > 0, f\"No automation targets found for {route['path']}\"",
        ]
    )
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

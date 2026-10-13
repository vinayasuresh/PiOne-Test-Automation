"""POST /scan router — the main API endpoint."""

import asyncio
import json
import os
import platform
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
    RouteReplayRequest,
    RegressionRunRequest,
    ScanSummary,
    SystemInfoResponse,
    LLMProviderInfo,
    TestContextResponse,
)
from api.services import crawler_service, mock_service
from api.services.llm_service import provider_catalog, request_plan
from api.services.scan_event_bus import get_channel, publish
from api.services.test_execution_service import execute_test_cases, load_saved_test_cases
from api.services.test_planning_service import build_test_cases
from api.services.test_context_service import create_context, decode_upload, load_context
from automation_framework.utils.logger import logger

router = APIRouter()

@router.post(
    "/scan",
    response_model=ScanResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request (bad URL, etc.)"},
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
        )
        return result

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
    llm_prompt = _build_llm_prompt(request, context, combined_prompt)
    planning_notes = await asyncio.to_thread(
        request_plan, request.provider, request.model, llm_prompt,
    )
    script = _build_pytest_script(
        base_url=request.base_url,
        scan=request.scan,
        description=combined_prompt,
        feature_focus=request.feature_focus or "",
    )
    return GeneratedTestResponse(
        script=script,
        execution_command="pytest generated_tests/test_generated_ui_flow.py",
        planning_notes=planning_notes,
        context_id=request.context_id,
        test_cases=build_test_cases(request.scan, context),
    )


@router.get("/llm/providers", response_model=list[LLMProviderInfo], summary="List configured test-planning providers")
async def list_llm_providers() -> list[LLMProviderInfo]:
    return [LLMProviderInfo(**item) for item in provider_catalog()]


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
    summary="Execute generated test plan against current scan intelligence",
)
async def execute_test(request: ExecuteTestRequest) -> ExecuteTestResponse:
    if not request.script.strip():
        raise HTTPException(status_code=400, detail="Generated script is required")
    test_cases = request.test_cases or build_test_cases(request.scan, {})
    if not test_cases:
        raise HTTPException(status_code=400, detail="No executable safe checks were derived from this scan")
    rows, summary, artifacts = await asyncio.to_thread(
        execute_test_cases, request.base_url, test_cases, request.allow_mutating_actions,
    )
    return ExecuteTestResponse(rows=rows, summary=summary, artifacts=artifacts)


@router.post("/regression-run", response_model=ExecuteTestResponse, summary="Rerun a saved controlled test plan")
async def regression_run(request: RegressionRunRequest) -> ExecuteTestResponse:
    try:
        test_cases = load_saved_test_cases(request.execution_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows, summary, artifacts = await asyncio.to_thread(
        execute_test_cases, request.base_url, test_cases, False,
    )
    artifacts["regression_of"] = request.execution_id
    return ExecuteTestResponse(rows=rows, summary=summary, artifacts=artifacts)


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

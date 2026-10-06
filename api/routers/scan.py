"""POST /scan router — the main API endpoint."""

import asyncio
import os
import platform
import re
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from api.models.scan_models import (
    ErrorResponse,
    ExecuteTestRequest,
    ExecuteTestResponse,
    ExecutionResultRow,
    ExecutionSummary,
    GenerateTestRequest,
    GeneratedTestResponse,
    ScanRequest,
    ScanResponse,
    RouteReplayRequest,
    ScanSummary,
    SystemInfoResponse,
)
from api.services import crawler_service, mock_service
from api.services.scan_event_bus import get_channel, publish
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

    script = _build_pytest_script(
        base_url=request.base_url,
        scan=request.scan,
        description=request.description or "",
        feature_focus=request.feature_focus or "",
    )
    return GeneratedTestResponse(
        script=script,
        execution_command="pytest generated_tests/test_generated_ui_flow.py",
    )


@router.post(
    "/execute-test",
    response_model=ExecuteTestResponse,
    summary="Execute generated test plan against current scan intelligence",
)
async def execute_test(request: ExecuteTestRequest) -> ExecuteTestResponse:
    if not request.script.strip():
        raise HTTPException(status_code=400, detail="Generated script is required")
    routes = request.scan.routes or []
    if not routes:
        raise HTTPException(status_code=400, detail="Scan data has no routes to execute")

    rows = _build_execution_rows(routes)
    passed = sum(1 for row in rows if row.Status == "Pass")
    failed = sum(1 for row in rows if row.Status == "Fail")
    return ExecuteTestResponse(
        rows=rows,
        summary=ExecutionSummary(total=len(rows), passed=passed, failed=failed),
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


def _build_execution_rows(routes) -> list[ExecutionResultRow]:
    rows: list[ExecutionResultRow] = []
    for index, route in enumerate(routes, start=1):
        components = route.components
        target_count = (
            len(components.buttons)
            + len(components.inputs)
            + len(components.dropdowns)
            + len(components.tables)
            + len(components.charts)
            + len(components.maps)
        )
        module = route.menu_name or route.path.strip("/") or "Home"
        feature = _feature_name(route.path, module)
        passed = target_count > 0
        rows.append(
            ExecutionResultRow(
                TC_ID=f"TC_{index:03d}",
                Screen=route.path or "/",
                Module=module,
                Feature=feature,
                Test_Case=f"Validate {module} route and automation targets",
                Expected_Result="Route has discoverable UI automation targets",
                Actual_Result=(
                    f"{target_count} automation targets available"
                    if passed
                    else "No automation targets were found"
                ),
                Status="Pass" if passed else "Fail",
                Failure_Info="" if passed else "Route needs stable controls or selectors",
                Bug_Priority="" if passed else "Medium",
            )
        )
    return rows


def _feature_name(path: str, fallback: str) -> str:
    parts = [p for p in re.split(r"[/_-]+", path or "") if p]
    if not parts:
        return fallback
    return " ".join(part.capitalize() for part in parts[-2:])

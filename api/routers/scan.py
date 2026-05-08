"""POST /scan router — the main API endpoint."""

import asyncio

from fastapi import APIRouter, HTTPException

from api.models.scan_models import ErrorResponse, ScanRequest, ScanResponse, ScanSummary
from api.services import crawler_service, mock_service
from automation_framework.utils.logger import logger

router = APIRouter()

# Wall-clock cap for a real (non-mock) scan.
SCAN_TIMEOUT_SECONDS = 180  # 3 minutes for a passive scan
# Interactive scans need a much larger budget — the user demonstrates flows
# on every route and may take a minute or more per page.
INTERACTIVE_TIMEOUT_SECONDS = 1800  # 30 minutes


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

    # ──────────────────────────────────────────────────────────────────
    # Real mode — run crawler in a background thread with a timeout.
    # ──────────────────────────────────────────────────────────────────
    timeout = INTERACTIVE_TIMEOUT_SECONDS if request.interactive_mode else SCAN_TIMEOUT_SECONDS
    try:
        result: ScanResponse = await asyncio.wait_for(
            asyncio.to_thread(
                crawler_service.run_scan,
                request.url,
                request.username,
                request.password,
                request.interactive_mode,
                request.interactive_timeout,
            ),
            timeout=timeout,
        )
        return result

    except asyncio.TimeoutError:
        msg = (
            f"Scan exceeded the {timeout}-second budget. "
            "The browser thread will continue in the background and shut "
            "down on its own; please retry with mock mode for an instant preview."
        )
        logger.error(f"[API] Scan timed out for {request.url}")
        # Return a structured 200 response with status=timeout so the UI
        # can render a friendly message instead of receiving an HTTP error.
        return ScanResponse(
            status="timeout",
            routes=[],
            summary=ScanSummary(
                total_routes=0,
                total_buttons=0,
                total_inputs=0,
                total_tables=0,
                scan_duration_seconds=float(timeout),
            ),
            message=msg,
        )

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

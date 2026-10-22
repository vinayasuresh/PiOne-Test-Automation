"""FastAPI application entry point.

Run with:
    uvicorn api.main:app --reload --port 8000

Frontend UI:
    http://localhost:8000/

Interactive API docs:
    http://localhost:8000/docs
"""

from pathlib import Path
import datetime
import json
import os
import subprocess

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import scan, scan_events
from automation_framework.config.settings import STORAGE_STATE_PATH
from automation_framework.utils.redactor import redact_text

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_REPORTS_DIR = Path(__file__).resolve().parents[1] / "automation_framework" / "reports"

PROCESS_START_TIMESTAMP = datetime.datetime.now(datetime.timezone.utc).isoformat()


def _get_build_id() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=str(Path(__file__).resolve().parent),
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "dev-local"


BUILD_IDENTIFIER = _get_build_id()
BROWSER_INSTALL_HINT = r"Run .venv\Scripts\python.exe -m playwright install chromium"


def _check_browser_readiness() -> dict[str, object]:
    """Verify that the pinned Playwright Chromium executable is installed."""
    try:
        browsers_manifest = (
            Path(__file__).resolve().parents[1]
            / ".venv"
            / "Lib"
            / "site-packages"
            / "playwright"
            / "driver"
            / "package"
            / "browsers.json"
        )
        manifest = json.loads(browsers_manifest.read_text(encoding="utf-8"))
        revision = next(
            str(item["revision"])
            for item in manifest.get("browsers", [])
            if item.get("name") == "chromium"
        )
        configured_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
        browser_root = (
            Path(configured_root)
            if configured_root and configured_root != "0"
            else Path.home() / "AppData" / "Local" / "ms-playwright"
        )
        candidates = list((browser_root / f"chromium-{revision}").glob("chrome-win*/chrome.exe"))
        if candidates and candidates[0].exists():
            return {"ready": True, "error": None}
        return {
            "ready": False,
            "error": f"Chromium executable for Playwright revision {revision} is missing. {BROWSER_INSTALL_HINT}",
        }
    except Exception:
        return {
            "ready": False,
            "error": f"Chromium readiness check failed. {BROWSER_INSTALL_HINT}",
        }


def _verify_and_clean_storage_template() -> None:
    template = {"cookies": [], "origins": []}
    try:
        if not STORAGE_STATE_PATH.exists():
            STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STORAGE_STATE_PATH.write_text(json.dumps(template), encoding="utf-8")
        else:
            try:
                data = json.loads(STORAGE_STATE_PATH.read_text(encoding="utf-8"))
                if data.get("cookies") or data.get("origins"):
                    STORAGE_STATE_PATH.write_text(json.dumps(template), encoding="utf-8")
            except Exception:
                STORAGE_STATE_PATH.write_text(json.dumps(template), encoding="utf-8")
    except Exception:
        pass


_verify_and_clean_storage_template()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PiOne Testmate API",
    description=(
        "PiOne Testmate — Deterministic UI Scanner, Test Intelligence Platform & Grounded Automation Framework."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Security & Abuse Limits Middleware
# ---------------------------------------------------------------------------

MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024  # 10 MB limit for incoming requests


@app.middleware("http")
async def security_and_limits_middleware(request: Request, call_next):
    # Enforce request body size limit via Content-Length header if provided
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={
                        "status": "error",
                        "detail": f"Request body too large. Maximum size is {MAX_REQUEST_BODY_SIZE // (1024 * 1024)} MB.",
                    },
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": "Invalid Content-Length header"},
            )

    response = await call_next(request)

    # Security Headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss: http: https:; "
        "font-src 'self' https://cdnjs.cloudflare.com; "
        "frame-ancestors 'none';"
    )
    return response


# ---------------------------------------------------------------------------
# CORS — keep the local UI usable while allowing an explicit deployment list.
# Wildcard origins are intentionally never combined with credentials.
_raw_cors = os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000")
_cors_origins = [
    item.strip().rstrip("/")
    for item in _raw_cors.split(",")
    if item.strip()
]
_allow_credentials = "*" not in _cors_origins
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(scan.router, tags=["Scan"])
app.include_router(scan_events.router, tags=["Scan Events"])

# ---------------------------------------------------------------------------
# Serve the frontend
# ---------------------------------------------------------------------------

# Serve extra static UI assets (images, css, etc.) strictly from frontend/
if _FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")


@app.get("/", include_in_schema=False)
async def serve_ui() -> FileResponse:
    """Serve the demo UI at the root URL."""
    return FileResponse(str(_FRONTEND_DIR / "index.html"))


# ---------------------------------------------------------------------------
# Global exception handler — always return JSON, sanitize paths & secrets
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    is_debug = os.getenv("DEBUG", "0").lower() in {"1", "true", "yes"}
    safe_msg = redact_text(str(exc))
    detail_str = f"{type(exc).__name__}: {safe_msg}" if is_debug else f"An internal error occurred ({type(exc).__name__})"
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": detail_str},
    )


# ---------------------------------------------------------------------------
# Health & version check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Health"], summary="Service health check")
def health() -> dict:
    browser = _check_browser_readiness()
    return {
        "status": "ok",
        "service": "PiOne Testmate API",
        "application": "PiOne Testmate",
        "version": "2.0.0",
        "build_id": BUILD_IDENTIFIER,
        "process_start": PROCESS_START_TIMESTAMP,
        "browser_ready": browser["ready"],
        "browser_error": browser["error"],
    }


@app.get("/version", tags=["Health"], summary="Service build and version details")
async def version() -> dict:
    return {
        "status": "ok",
        "application": "PiOne Testmate",
        "version": "2.0.0",
        "build_id": BUILD_IDENTIFIER,
        "process_start": PROCESS_START_TIMESTAMP,
    }

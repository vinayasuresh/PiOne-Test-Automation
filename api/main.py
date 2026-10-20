"""FastAPI application entry point.

Run with:
    uvicorn api.main:app --reload --port 8000

Frontend UI:
    http://localhost:8000/

Interactive API docs:
    http://localhost:8000/docs
"""

from pathlib import Path
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import scan, scan_events
from automation_framework.utils.redactor import redact_text

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_REPORTS_DIR = Path(__file__).resolve().parents[1] / "automation_framework" / "reports"

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
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Health"], summary="Service health check")
async def health() -> dict:
    return {"status": "ok", "service": "PiOne Testmate API", "version": "2.0.0"}

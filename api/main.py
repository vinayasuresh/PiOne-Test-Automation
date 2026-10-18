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

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_REPORTS_DIR = Path(__file__).resolve().parents[1] / "automation_framework" / "reports"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="UI Crawler API",
    description=(
        "Scans web applications and extracts UI component intelligence "
        "(buttons, inputs, tables, forms, filters) per route."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS — keep the local UI usable while allowing an explicit deployment list.
# Wildcard origins are intentionally not combined with credentials.
_cors_origins = [
    item.strip().rstrip("/")
    for item in os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    if item.strip()
]
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
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

# Serve any extra static assets (images, css, etc.) from frontend/
if _FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")
if _REPORTS_DIR.exists():
    # Reports are generated inside the controlled workspace and exposed only
    # by opaque run-id filenames; this enables the UI's download/evidence links.
    app.mount("/reports", StaticFiles(directory=str(_REPORTS_DIR)), name="reports")


@app.get("/", include_in_schema=False)
async def serve_ui() -> FileResponse:
    """Serve the demo UI at the root URL."""
    return FileResponse(str(_FRONTEND_DIR / "index.html"))

# ---------------------------------------------------------------------------
# Global exception handler — always return JSON, never raw HTML
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": f"{type(exc).__name__}: {exc}"},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Health"], summary="Service health check")
async def health() -> dict:
    return {"status": "ok", "service": "UI Crawler API", "version": "2.0.0"}

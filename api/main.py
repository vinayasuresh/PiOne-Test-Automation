"""FastAPI application entry point.

Run with:
    uvicorn api.main:app --reload --port 8000

Frontend UI:
    http://localhost:8000/

Interactive API docs:
    http://localhost:8000/docs
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import scan

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="UI Crawler API",
    description=(
        "Scans web applications and extracts UI component intelligence "
        "(buttons, inputs, tables, forms, filters) per route."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS — allow all origins so any frontend can call this during development.
# Tighten allow_origins in production.
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(scan.router, tags=["Scan"])

# ---------------------------------------------------------------------------
# Serve the frontend
# ---------------------------------------------------------------------------

# Serve any extra static assets (images, css, etc.) from frontend/
if _FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")


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
    return {"status": "ok", "service": "UI Crawler API", "version": "1.0.0"}

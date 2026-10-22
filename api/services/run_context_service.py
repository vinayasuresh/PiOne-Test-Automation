"""run_context_service.py: Server-side persistence and coordination of RunContext.

Guarantees:
- Thread-safe in-memory cache and atomic file storage.
- Strict run ID validation to prevent path traversal.
- Credential and secret redaction before writing to disk.
- Ensures all stages (discovery, scan, plan, execute, regression) reference the same run context.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from api.models.run_context import RunContext
from automation_framework.config.settings import REPORT_PATH
from automation_framework.utils.logger import logger
from automation_framework.utils.redactor import redact_sensitive_data

_RUN_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_RUN_STATUSES = {
    "discovered",
    "running",
    "paused",
    "partial_scanned",
    "scanned",
    "executing",
    "executed",
    "failed",
}

_RUN_CONTEXT_DIR = REPORT_PATH / "run_contexts"
_RUN_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()
_CACHE: dict[str, RunContext] = {}


def generate_run_id() -> str:
    return uuid4().hex


def validate_run_id_safe(run_id: str | None) -> str:
    """Validate that run_id is alphanumeric with dashes/underscores and free of traversal."""
    if not run_id or not isinstance(run_id, str):
        raise ValueError("Invalid run ID: run_id must be a non-empty string")
    clean = run_id.strip()
    if not _RUN_ID_REGEX.fullmatch(clean) or ".." in clean or "/" in clean or "\\" in clean:
        raise ValueError(f"Invalid run ID format: '{run_id}'")
    return clean


def create_run_context(
    target_origin: str | None = None,
    allowed_path_namespace: str = "/",
    run_id: str | None = None,
    username: str | None = None,
    auth_session_id: str | None = None,
    url: str | None = None,
) -> RunContext:
    origin = target_origin or url or ""
    rid = validate_run_id_safe(run_id) if run_id else generate_run_id()
    ctx = RunContext(
        run_id=rid,
        scan_id=rid,
        target_origin=origin,
        allowed_path_namespace=allowed_path_namespace,
        auth_session_id=auth_session_id,
        username=username,
        status="discovered",
    )
    save_run_context(ctx)
    return ctx


def save_run_context(ctx: RunContext) -> None:
    validate_run_id_safe(ctx.run_id)
    if not ctx.scan_id:
        ctx.scan_id = ctx.run_id
    if ctx.status not in _RUN_STATUSES:
        if ctx.status in {"initialized", "created"}:
            ctx.status = "discovered"
        elif ctx.status in {"scanning", "waiting_approval", "stopping"}:
            ctx.status = "running"
        elif ctx.status in {"completed", "success"}:
            ctx.status = "scanned"
        elif ctx.status == "discarded":
            ctx.status = "failed"
    ctx.update_timestamp()
    with _LOCK:
        _CACHE[ctx.run_id] = ctx
        file_path = _RUN_CONTEXT_DIR / f"{ctx.run_id}.json"
        tmp_path = _RUN_CONTEXT_DIR / f"{ctx.run_id}.json.tmp"
        try:
            dumped = ctx.model_dump(mode="json")
            safe_dumped = redact_sensitive_data(dumped)
            tmp_path.write_text(json.dumps(safe_dumped, indent=2, default=str), encoding="utf-8")
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            tmp_path.replace(file_path)
        except Exception as exc:
            logger.warning("Failed saving run context to disk: %s", exc)

    try:
        from api.db.repository import DBRepository
        DBRepository.create_or_update_run(
            run_id=ctx.run_id,
            base_url=ctx.target_origin,
            allowed_path_namespace=ctx.allowed_path_namespace,
            status=ctx.status,
            auth_status=ctx.auth_status,
            username=ctx.username,
            selected_modules=ctx.selected_modules,
            discovered_modules=ctx.discovered_modules,
            execution_summary=ctx.execution_summary or {},
            error_message=ctx.error_message,
        )
    except Exception as db_exc:
        logger.debug("[DB] Secondary run context sync note: %s", db_exc)


def list_saved_runs(base_url: str | None = None, is_archived: bool = False, skip: int = 0, limit: int = 50) -> list[dict[str, Any]]:
    """List saved runs from DB repository with fallback to file store."""
    try:
        from api.db.repository import DBRepository
        runs = DBRepository.list_runs(base_url=base_url, is_archived=is_archived, skip=skip, limit=limit)
        if runs:
            # The relational store keeps the run index, while the atomic
            # RunContext file is the source of truth for live checkpoint
            # progress.  Merge terminal module/route state back into the DB
            # rows so Saved Runs cannot show a completed scan as pending.
            merged: list[dict[str, Any]] = []
            for row in runs:
                run_id = row.get("run_id")
                ctx = get_run_context(run_id) if run_id else None
                if ctx:
                    graph = ctx.route_graph if isinstance(ctx.route_graph, dict) else {}
                    graph_nodes = graph.get("nodes")
                    route_count = (
                        len(graph_nodes)
                        if isinstance(graph_nodes, list)
                        else len(graph)
                    )
                    row = {
                        **row,
                        "status": ctx.status,
                        "selected_modules": list(ctx.selected_modules),
                        "completed_modules": list(ctx.completed_modules),
                        "pending_modules": list(ctx.pending_modules),
                        "progress_percentage": (
                            100.0
                            if ctx.status in {"scanned", "executed"}
                            else row.get("progress_percentage", 0.0)
                        ),
                        "route_count": (
                            route_count if graph else row.get("route_count", 0)
                        ),
                        "updated_at": ctx.updated_at or row.get("updated_at"),
                    }
                merged.append(row)
            return merged
    except Exception as exc:
        logger.debug("[DB] list_runs fallback: %s", exc)

    results = []
    for f in sorted(_RUN_CONTEXT_DIR.glob("*.json"), key=os.path.getmtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if is_archived and not data.get("is_archived"):
                continue
            if not is_archived and data.get("is_archived"):
                continue
            if base_url and base_url.lower() not in (data.get("target_origin") or "").lower():
                continue
            results.append({
                "run_id": data.get("run_id"),
                "application_url": data.get("target_origin"),
                "status": data.get("status", "initialized"),
                "auth_status": data.get("auth_status", "none"),
                "progress_percentage": 100.0 if data.get("status") in {"scanned", "executed"} else 50.0,
                "selected_modules": data.get("selected_modules", []),
                "completed_modules": data.get("selected_modules", []) if data.get("status") in {"scanned", "executed"} else [],
                "pending_modules": [] if data.get("status") in {"scanned", "executed"} else data.get("selected_modules", []),
                "route_count": len(data.get("route_graph", {}).get("nodes", [])),
                "execution_summary": data.get("execution_summary", {}),
                "is_archived": bool(data.get("is_archived")),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
            })
        except Exception:
            pass
    return results[skip : skip + limit]


def get_run_context(run_id: str) -> RunContext | None:
    try:
        clean_id = validate_run_id_safe(run_id)
    except ValueError:
        return None

    with _LOCK:
        if clean_id in _CACHE:
            return _CACHE[clean_id]

    file_path = _RUN_CONTEXT_DIR / f"{clean_id}.json"
    if file_path.is_file():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            ctx = RunContext.model_validate(data)
            with _LOCK:
                _CACHE[clean_id] = ctx
            return ctx
        except Exception as exc:
            logger.error("Failed loading run context from %s: %s", file_path, exc)
    return None


def require_run_context(run_id: str) -> RunContext:
    ctx = get_run_context(run_id)
    if not ctx:
        raise ValueError(f"Run context '{run_id}' not found. Please discover modules first.")
    return ctx


def update_run_context(run_id: str, **kwargs: Any) -> RunContext:
    ctx = require_run_context(run_id)
    for k, v in kwargs.items():
        if hasattr(ctx, k):
            setattr(ctx, k, v)
    save_run_context(ctx)
    return ctx


def delete_run_context(run_id: str) -> bool:
    try:
        clean_id = validate_run_id_safe(run_id)
    except ValueError:
        return False
    with _LOCK:
        _CACHE.pop(clean_id, None)
    file_path = _RUN_CONTEXT_DIR / f"{clean_id}.json"
    if file_path.is_file():
        try:
            file_path.unlink()
            return True
        except Exception as exc:
            logger.warning("Failed deleting run context file %s: %s", file_path, exc)
    return False

"""run_context_service.py: Server-side persistence and coordination of RunContext.

Guarantees:
- Thread-safe in-memory cache and atomic file storage.
- Ensures all stages (discovery, scan, plan, execute, regression) reference the same run context.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from api.models.run_context import RunContext
from automation_framework.config.settings import REPORT_PATH
from automation_framework.utils.logger import logger

_RUN_CONTEXT_DIR = REPORT_PATH / "run_contexts"
_RUN_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()
_CACHE: dict[str, RunContext] = {}


def generate_run_id() -> str:
    return uuid4().hex


def create_run_context(
    target_origin: str | None = None,
    allowed_path_namespace: str = "/",
    run_id: str | None = None,
    username: str | None = None,
    auth_session_id: str | None = None,
    url: str | None = None,
) -> RunContext:
    origin = target_origin or url or ""
    rid = run_id or generate_run_id()
    ctx = RunContext(
        run_id=rid,
        target_origin=origin,
        allowed_path_namespace=allowed_path_namespace,
        auth_session_id=auth_session_id,
        username=username,
        status="discovered",
    )
    save_run_context(ctx)
    return ctx


def save_run_context(ctx: RunContext) -> None:
    ctx.update_timestamp()
    with _LOCK:
        _CACHE[ctx.run_id] = ctx
        file_path = _RUN_CONTEXT_DIR / f"{ctx.run_id}.json"
        tmp_path = _RUN_CONTEXT_DIR / f"{ctx.run_id}.json.tmp"
        try:
            tmp_path.write_text(json.dumps(ctx.model_dump(mode="json"), indent=2, default=str), encoding="utf-8")
            tmp_path.replace(file_path)
        except Exception as exc:
            logger.warning("Failed saving run context to disk: %s", exc)


def get_run_context(run_id: str) -> RunContext | None:
    with _LOCK:
        if run_id in _CACHE:
            return _CACHE[run_id]

    file_path = _RUN_CONTEXT_DIR / f"{run_id}.json"
    if file_path.is_file():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            ctx = RunContext.model_validate(data)
            with _LOCK:
                _CACHE[run_id] = ctx
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

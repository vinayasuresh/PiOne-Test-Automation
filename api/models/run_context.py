"""run_context.py: Immutable run context schema for end-to-end automation lifecycle.

Guarantees:
- Every scan, review, test generation, execution, and regression references this RunContext.
- Binds target origin, path namespace, auth session, element registry, test plans, and artifacts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field


class RunContext(BaseModel):
    run_id: str
    scan_id: Optional[str] = None
    target_origin: str
    allowed_path_namespace: str = "/"
    auth_session_id: Optional[str] = None
    auth_status: str = "none"  # "authenticated" | "expired" | "none"
    username: Optional[str] = None
    selected_modules: list[str] = Field(default_factory=list)
    discovered_modules: list[dict[str, Any]] = Field(default_factory=list)
    route_graph: dict[str, Any] = Field(default_factory=dict)
    ui_state_graph: dict[str, Any] = Field(default_factory=dict)
    element_registry: Any = Field(default_factory=list)
    scan_results: Optional[dict[str, Any]] = None
    generated_test_plan: Optional[list[Any]] = None
    generated_script: Optional[str] = None
    execution_results: Optional[list[dict[str, Any]]] = None
    execution_summary: Optional[dict[str, Any]] = None
    checkpoints: list[str] = Field(default_factory=list)
    artifact_paths: dict[str, Any] = Field(default_factory=dict)
    status: str = "initialized"
    current_module: Optional[str] = None
    current_route: Optional[str] = None
    route_progress: dict[str, int] = Field(default_factory=lambda: {"scanned": 0, "pending": 0, "total": 0})
    elements_found: int = 0
    safe_actions_explored: int = 0
    parking_lot_actions: int = 0
    retry_count: int = 0
    last_event_sequence: int = 0
    checkpoint_version: int = 0
    is_archived: bool = False
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def update_timestamp(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

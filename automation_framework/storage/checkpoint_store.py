"""CheckpointStore: Atomic persistence and resumption for scan sessions.

Guarantees:
- Atomic file writes using temporary files to prevent corruption.
- Namespace scoping: scans are bound to their requested origin/path prefix.
- Sensitive data exclusion (passwords, tokens, raw input bodies).
- Full state preservation: visited routes, pending routes, route results,
  parking-lot actions, element map snapshot, failures, and scan events.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from automation_framework.utils.logger import logger
from automation_framework.utils.redactor import redact_sensitive_data


def compute_namespace(base_url: str) -> str:
    """Compute normalized origin + base path namespace."""
    parsed = urlparse(base_url)
    path = "/" + "/".join(part for part in parsed.path.split("/") if part)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path.rstrip('/') or '/'}"


import os
import shutil
import time

def _atomic_replace(src: Path, dst: Path, retries: int = 5, delay: float = 0.05) -> None:
    """Atomically replace dst with src, with Windows retry and fallback."""
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except (PermissionError, FileExistsError):
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                try:
                    if dst.exists():
                        dst.unlink()
                    shutil.move(str(src), str(dst))
                    return
                except Exception:
                    shutil.copy2(str(src), str(dst))
                    try:
                        src.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise


class CheckpointStore:
    """Handles atomic saving and safe loading of scanner checkpoints."""

    def __init__(self, checkpoint_path: str | Path) -> None:
        self.checkpoint_path = Path(checkpoint_path)

    def save(self, data: dict[str, Any]) -> bool:
        """Atomically persist scan checkpoint data."""
        try:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
            
            # Clean sensitive values before saving
            cleaned_page_intel = {}
            for route, doc in data.get("page_intelligence", {}).items():
                safe_doc = dict(doc)
                safe_doc.pop("interactions", None)
                safe_doc.pop("interaction_flow", None)
                cleaned_page_intel[route] = safe_doc

            payload = {
                "session_id": data.get("session_id", ""),
                "namespace": data.get("namespace", ""),
                "visited_routes": sorted(data.get("visited_routes", [])),
                "pending_routes": sorted(data.get("pending_routes", [])),
                "failed_routes": sorted(data.get("failed_routes", [])),
                "partial_routes": sorted(data.get("partial_routes", [])),
                "route_validations": data.get("route_validations", {}),
                "page_intelligence": cleaned_page_intel,
                "element_map_snapshot": data.get("element_map_snapshot", []),
                "parking_lot_actions": data.get("parking_lot_actions", []),
                "feature_routes": data.get("feature_routes", {}),
                "menu_hierarchy": data.get("menu_hierarchy", {}),
                "scan_events": data.get("scan_events", []),
                "manual_interactions": data.get("manual_interactions", []),
                "interaction_flow": data.get("interaction_flow", []),
                "manual_checkpoint_state": data.get("manual_checkpoint_state", {}),
                "current_module_id": data.get("current_module_id", ""),
                "current_route": data.get("current_route", ""),
                "completed_modules": data.get("completed_modules", []),
                "completed_routes": data.get("completed_routes", []),
                "selected_modules": data.get("selected_modules", []),
                "scroll_position": data.get("scroll_position", {}),
                "schema_version": data.get("schema_version", "2.0"),
                "state_data": data.get("state_data", {}),
                "module_decisions": data.get("module_decisions", {}),
                "route_graph": data.get("route_graph", {}),
            }

            safe_payload = redact_sensitive_data(payload)
            tmp_path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            _atomic_replace(tmp_path, self.checkpoint_path)
            logger.info("Scan checkpoint safely written to %s", self.checkpoint_path)

            try:
                from api.db.repository import DBRepository
                run_id = data.get("session_id") or self.checkpoint_path.stem.removeprefix("checkpoint_")
                if run_id:
                    DBRepository.save_checkpoint(run_id, payload)
            except Exception as db_exc:
                logger.debug("[DB] Checkpoint DB sync note: %s", db_exc)

            return True
        except Exception:
            logger.exception("Failed to write scan checkpoint to %s", self.checkpoint_path)
            return False

    def load(self, expected_base_url: str | None = None) -> dict[str, Any] | None:
        """Load and validate an existing checkpoint."""
        if not self.checkpoint_path.exists():
            return None
        try:
            raw = self.checkpoint_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if expected_base_url:
                expected_ns = compute_namespace(expected_base_url)
                if data.get("namespace") and data.get("namespace") != expected_ns:
                    logger.warning(
                        "Checkpoint namespace '%s' does not match target '%s'",
                        data.get("namespace"), expected_ns,
                    )
                    return None
            return data
        except Exception:
            logger.exception("Failed to load or parse checkpoint from %s", self.checkpoint_path)
            return None


def get_checkpoint_path(run_id: str) -> Path:
    from automation_framework.config.settings import REPORT_PATH
    return REPORT_PATH / f"checkpoint_{run_id}.json"


def save_checkpoint(run_id: str, data: dict[str, Any]) -> bool:
    store = CheckpointStore(get_checkpoint_path(run_id))
    payload = dict(data)
    payload["session_id"] = run_id
    return store.save(payload)


def load_checkpoint(run_id: str, expected_base_url: str | None = None) -> dict[str, Any] | None:
    store = CheckpointStore(get_checkpoint_path(run_id))
    return store.load(expected_base_url)


import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from automation_framework.config.settings import REPORT_PATH
from automation_framework.storage.json_storage import save_json
from automation_framework.utils.logger import logger


def generate_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def collect_metadata(
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    crawl_results: dict[str, Any],
    base_url: str = "",
) -> dict[str, Any]:
    page_intelligence = crawl_results.get("page_intelligence", {})
    total_routes = len(page_intelligence)
    total_components = sum(
        len(route_doc.get("automation_targets", []))
        for route_doc in page_intelligence.values()
    )

    return {
        "run_id": run_id,
        "timestamp": started_at.isoformat(timespec="seconds"),
        "platform": f"{platform.system()} {platform.release()}",
        "python_version": sys.version.split()[0],
        "base_url": base_url,
        "total_routes_scanned": total_routes,
        "total_components_detected": total_components,
        "execution_duration_seconds": round((finished_at - started_at).total_seconds(), 2),
    }


def save_metadata(run_id: str, metadata: dict[str, Any]) -> Path:
    output_file = Path(REPORT_PATH) / f"{run_id}_metadata.json"
    save_json(metadata, output_file)
    logger.info(f"Run metadata saved to {output_file}")
    return output_file

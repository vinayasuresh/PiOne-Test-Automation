from pathlib import Path
from typing import Any

from automation_framework.config.settings import REPORT_PATH
from automation_framework.engine.ui_intelligence_engine import SCHEMA_VERSION
from automation_framework.storage.json_storage import save_json
from automation_framework.storage.yaml_storage import save_yaml
from automation_framework.utils.logger import logger


def export_crawl_results(
    crawl_results: dict[str, Any],
    output_name: str = "crawl_results",
    output_dir: str | Path = REPORT_PATH,
    run_id: str | None = None,
) -> dict[str, Path]:
    """Write the normalized UI intelligence schema to disk (JSON + YAML)."""
    output_path = Path(output_dir)
    file_stem = f"{run_id}_{output_name}" if run_id else output_name
    json_file = output_path / f"{file_stem}.json"
    yaml_file = output_path / f"{file_stem}.yaml"

    page_intelligence = crawl_results.get("page_intelligence", {})
    routes = [_normalize_route(route_doc) for route_doc in page_intelligence.values()]

    export_data = {
        "schema_version": SCHEMA_VERSION,
        "routes": routes,
        "component_registry": crawl_results.get("component_registry", {}),
        "manual_interactions": crawl_results.get("manual_interactions", []),
    }

    save_json(export_data, json_file)
    save_yaml(export_data, yaml_file)

    logger.info(f"Crawl results exported to {json_file} and {yaml_file}")

    return {"json": json_file, "yaml": yaml_file}


def _normalize_route(route_doc: dict[str, Any]) -> dict[str, Any]:
    """Guarantee every route has the required keys in a stable order."""
    return {
        "route": route_doc.get("route", ""),
        "menu_name": route_doc.get("menu_name", ""),
        "purpose": route_doc.get("purpose", ""),
        "sections": route_doc.get("sections", []),
        "automation_targets": route_doc.get("automation_targets", []),
    }

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
    routes_file = output_path / f"{file_stem}_routes.json"
    elements_file = output_path / f"{file_stem}_elements.json"
    interaction_flow_file = output_path / f"{file_stem}_interaction_flow.yaml"

    page_intelligence = crawl_results.get("page_intelligence", {})
    routes = [_normalize_route(route_doc) for route_doc in page_intelligence.values()]
    elements = _flatten_elements(page_intelligence)
    interaction_flow = crawl_results.get("interaction_flow", [])

    export_data = {
        "schema_version": SCHEMA_VERSION,
        "routes": routes,
        "component_registry": crawl_results.get("component_registry", {}),
        "manual_interactions": crawl_results.get("manual_interactions", []),
        "interaction_flow": interaction_flow,
    }

    save_json(export_data, json_file)
    save_yaml(export_data, yaml_file)
    save_json({"routes": routes}, routes_file)
    save_json({"elements": elements}, elements_file)
    save_yaml({"interaction_flow": interaction_flow}, interaction_flow_file)

    logger.info(
        "Crawl results exported to "
        f"{json_file}, {yaml_file}, {routes_file}, {elements_file}, {interaction_flow_file}"
    )

    return {
        "json": json_file,
        "yaml": yaml_file,
        "routes": routes_file,
        "elements": elements_file,
        "interaction_flow": interaction_flow_file,
    }


def _normalize_route(route_doc: dict[str, Any]) -> dict[str, Any]:
    """Guarantee every route has the required keys in a stable order."""
    return {
        "route": route_doc.get("route", ""),
        "menu_name": route_doc.get("menu_name", ""),
        "purpose": route_doc.get("purpose", ""),
        "framework": route_doc.get("framework", ""),
        "sections": route_doc.get("sections", []),
        "auto_detected_elements": route_doc.get("auto_detected_elements", []),
        "user_confirmed_elements": route_doc.get("user_confirmed_elements", []),
        "automation_targets": route_doc.get("automation_targets", []),
        "interactions": route_doc.get("interactions", []),
        "interaction_flow": route_doc.get("interaction_flow", []),
    }


def _flatten_elements(page_intelligence: dict[str, Any]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for route_url, route_doc in page_intelligence.items():
        for target in route_doc.get("automation_targets", []):
            elements.append(
                {
                    "route": route_url,
                    "page": route_doc.get("menu_name", ""),
                    "type": target.get("type", ""),
                    "category": target.get("category", ""),
                    "action_type": target.get("interaction_type", ""),
                    "text": target.get("text") or target.get("label", ""),
                    "selector": target.get("selector", {}),
                    "framework": target.get("framework", route_doc.get("framework", "")),
                    "framework_type": target.get("framework_type", ""),
                    "component_tag": target.get("component_tag", ""),
                    "is_dynamic": target.get("is_dynamic", False),
                    "is_user_confirmed": target.get("is_user_confirmed", False),
                }
            )
    return elements

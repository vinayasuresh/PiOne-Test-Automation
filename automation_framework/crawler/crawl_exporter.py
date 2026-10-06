from pathlib import Path
from typing import Any
import json

import pandas as pd

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
    excel_file = output_path / f"{file_stem}.xlsx"

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
        "route_dependency_map": _route_dependency_map(crawl_results),
        "menu_hierarchy": crawl_results.get("menu_hierarchy", {}),
        "route_namespace": crawl_results.get("route_namespace", ""),
    }

    save_json(export_data, json_file)
    save_yaml(export_data, yaml_file)
    save_json({"routes": routes}, routes_file)
    save_json({"elements": elements}, elements_file)
    save_yaml({"interaction_flow": interaction_flow}, interaction_flow_file)
    _export_excel(crawl_results, elements, excel_file)

    logger.info(
        "Crawl results exported to "
        f"{json_file}, {yaml_file}, {routes_file}, {elements_file}, {interaction_flow_file}, {excel_file}"
    )

    return {
        "json": json_file,
        "yaml": yaml_file,
        "routes": routes_file,
        "elements": elements_file,
        "interaction_flow": interaction_flow_file,
        "excel": excel_file,
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
        "route_validation": route_doc.get("route_validation", {}),
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
                    "element_xpath": target.get("element_xpath", ""),
                    "element_role": target.get("element_role", ""),
                    "element_index": target.get("element_index", 0),
                    "element_identity": target.get("element_identity") or "|".join((
                        route_url, target.get("element_xpath", ""),
                        target.get("element_role", ""), target.get("label", ""),
                        str(target.get("element_index", 0)),
                    )),
                    "framework": target.get("framework", route_doc.get("framework", "")),
                    "framework_type": target.get("framework_type", ""),
                    "component_tag": target.get("component_tag", ""),
                    "is_dynamic": target.get("is_dynamic", False),
                    "is_user_confirmed": target.get("is_user_confirmed", False),
                    "element_type": target.get("type", ""),
                    "interaction_probability": target.get("interaction_probability", 0.0),
                    "interaction_probability_method": target.get("interaction_probability_method", ""),
                    "hierarchy_level": target.get("hierarchy_level", 2),
                }
            )
    return elements


def _export_excel(crawl_results: dict[str, Any], elements: list[dict[str, Any]], path: Path) -> None:
    """Export the required, deterministic scan views to one workbook."""
    validations = crawl_results.get("route_validations", {}) or {}
    route_rows = []
    for route, validation in sorted(validations.items()):
        row = {"route": route, **validation}
        row["score_components"] = json.dumps(validation.get("score_components", {}), sort_keys=True)
        route_rows.append(row)
    hierarchy = _flatten_menu_hierarchy(crawl_results.get("menu_hierarchy", {}))
    if not hierarchy:
        hierarchy = [
        {"route": route, "menu_name": doc.get("menu_name", ""),
         "purpose": doc.get("purpose", ""), "framework": doc.get("framework", ""),
         "hierarchy_level": 1}
        for route, doc in sorted((crawl_results.get("page_intelligence", {}) or {}).items())
        ]
    columns = ["route", "score", "status", "expected_url", "actual_url", "redirected",
               "page_load_success", "document_status", "dom_stable", "api_response_present",
               "failed_api_responses", "target_count", "interaction_success_rate", "score_components"]
    all_routes = pd.DataFrame(route_rows, columns=columns)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for status, sheet in (
            ("WORKING", "working_routes"),
            ("NON-WORKING", "non_working_routes"),
            ("PARTIAL", "partial_routes"),
        ):
            all_routes[all_routes.get("status", pd.Series(dtype=str)) == status].to_excel(
                writer, sheet_name=sheet, index=False
            )
        pd.DataFrame(hierarchy).to_excel(writer, sheet_name="menu_hierarchy", index=False)
        pd.DataFrame(elements).to_excel(writer, sheet_name="element_map", index=False)
        pd.DataFrame(_route_dependency_map(crawl_results)).to_excel(
            writer, sheet_name="route_dependency_map", index=False
        )


def _route_dependency_map(crawl_results: dict[str, Any]) -> list[dict[str, Any]]:
    """Represent menu-derived route links with the observed entry namespace."""
    return [
        {"from_route": "application_entry", "to_route": route,
         "label": item.get("label", ""), "source": item.get("source", ""),
         "hierarchy_level": item.get("hierarchy_level", 0), "relation": "menu_link"}
        for route, item in sorted((crawl_results.get("feature_routes", {}) or {}).items())
    ]


def _flatten_menu_hierarchy(menu_hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], source: str, parent: str = "") -> None:
        label = node.get("text", "")
        rows.append({"route": node.get("href") or "", "menu_name": label,
                     "parent_menu": parent, "source": source,
                     "hierarchy_level": node.get("hierarchy_level", 0),
                     "selector": node.get("selector", "")})
        for child in node.get("children", []):
            walk(child, source, label)

    for menu_type, groups in menu_hierarchy.items():
        for group in groups:
            for root in group.get("tree", []):
                walk(root, menu_type)
    return rows

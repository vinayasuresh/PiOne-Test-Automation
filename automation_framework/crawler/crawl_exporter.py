from pathlib import Path
from typing import Any
import json

import pandas as pd

from automation_framework.config.settings import REPORT_PATH
from automation_framework.crawler.route_health_scorer import (
    is_working_route,
    is_partial_route,
    is_non_working_route,
)
from automation_framework.engine.ui_intelligence_engine import SCHEMA_VERSION
from automation_framework.storage.json_storage import save_json
from automation_framework.storage.yaml_storage import save_yaml
from automation_framework.utils.logger import logger
from automation_framework.utils.redactor import redact_sensitive_data


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
    markdown_file = output_path / f"{file_stem}_summary.md"

    # Redact credentials and tokens before exporting any artifacts
    crawl_results = redact_sensitive_data(crawl_results)

    page_intelligence = crawl_results.get("page_intelligence", {})
    routes = [_normalize_route(route_doc) for route_doc in page_intelligence.values()]
    elements = _flatten_elements(page_intelligence)
    interaction_flow = crawl_results.get("interaction_flow", [])
    parking_lot = crawl_results.get("parking_lot_actions", [])
    scan_events = crawl_results.get("scan_events", [])

    export_data = {
        "schema_version": SCHEMA_VERSION,
        "routes": routes,
        "scan_summary": crawl_results.get("scan_summary", {}),
        "route_validations": crawl_results.get("route_validations", {}),
        "test_cases": crawl_results.get("test_cases", []),
        "execution_results": crawl_results.get("execution_results", []),
        "component_registry": crawl_results.get("component_registry", {}),
        "manual_interactions": crawl_results.get("manual_interactions", []),
        "interaction_flow": interaction_flow,
        "parking_lot_actions": parking_lot,
        "scan_events": scan_events,
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
    _export_markdown(crawl_results, elements, markdown_file)

    logger.info(
        "Crawl results exported to "
        f"{json_file}, {yaml_file}, {routes_file}, {elements_file}, {interaction_flow_file}, {excel_file}, {markdown_file}"
    )

    return {
        "json": json_file,
        "yaml": yaml_file,
        "routes": routes_file,
        "elements": elements_file,
        "interaction_flow": interaction_flow_file,
        "excel": excel_file,
        "markdown": markdown_file,
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
    seen_keys: set[str] = set()
    for route_url, route_doc in page_intelligence.items():
        menu_name = route_doc.get("menu_name", "")
        for target in route_doc.get("automation_targets", []):
            ident = target.get("element_identity") or target.get("id") or target.get("element_id") or "|".join((
                route_url, target.get("element_xpath", ""),
                target.get("element_role", ""), target.get("label", ""),
                str(target.get("element_index", 0)),
            ))
            label = target.get("label") or target.get("text", "")
            role = target.get("element_role") or target.get("role") or target.get("type", "")
            xpath = target.get("element_xpath") or target.get("xpath", "")
            # DOM indexes and telemetry sequence numbers are unstable. Use the
            # route, stable selector, role, label, and interaction type as the
            # export identity so repeated probes cannot create duplicate targets.
            stable_key = "|".join((
                str(route_url).strip().lower(),
                str(xpath).strip(),
                str(role).strip().lower(),
                " ".join(str(label).split()).strip().lower(),
                str(target.get("interaction_type") or target.get("action_type") or "").strip().lower(),
            ))
            if stable_key in seen_keys:
                continue
            seen_keys.add(stable_key)
            category = target.get("category", "")
            hierarchy_path = target.get("hierarchy_path") or (f"{menu_name} > {category}" if menu_name and category else (menu_name or category or "default"))
            module_id = target.get("module_id") or f"route:{route_url}"
            ui_state_id = target.get("ui_state_id") or "state_base"
            discovery_mechanism = target.get("discovery_mechanism") or "passive_dom"
            quality_score = target.get("quality_score", 50)
            safety_classification = target.get("safety_classification", "UNKNOWN")

            elements.append(
                {
                    "element_id": ident,
                    "route": route_url,
                    "module_id": module_id,
                    "ui_state_id": ui_state_id,
                    "hierarchy_path": hierarchy_path,
                    "role": role,
                    "label": label,
                    "selector": target.get("selector", {}),
                    "xpath": xpath,
                    "discovery_mechanism": discovery_mechanism,
                    "quality_score": quality_score,
                    "safety_classification": safety_classification,
                    "page": menu_name,
                    "type": target.get("type", ""),
                    "category": category,
                    "action_type": target.get("interaction_type", ""),
                    "text": label,
                    "element_xpath": xpath,
                    "element_role": role,
                    "element_index": target.get("element_index", 0),
                    "element_identity": ident,
                    "framework": target.get("framework", route_doc.get("framework", "")),
                    "framework_type": target.get("framework_type", ""),
                    "component_tag": target.get("component_tag", ""),
                    "is_dynamic": target.get("is_dynamic", False),
                    "is_user_confirmed": target.get("is_user_confirmed", False),
                    "element_type": target.get("type", ""),
                    "interaction_probability": target.get("interaction_probability", 0.0),
                    "interaction_probability_method": target.get("interaction_probability_method", ""),
                    "hierarchy_level": target.get("hierarchy_level", 2),
                    "source": target.get("source", "automatic_scan"),
                    "source_page": target.get("source_page", route_url),
                    "source_html": target.get("source_html", ""),
                }
            )
    return elements


def _export_excel(crawl_results: dict[str, Any], elements: list[dict[str, Any]], path: Path) -> None:
    """Export the 10 required deterministic scan, planning, and execution views to one workbook."""
    validations = dict(crawl_results.get("route_validations", {}) or {})
    page_intel = crawl_results.get("page_intelligence", {}) or {}
    for r_url, r_doc in page_intel.items():
        if r_url not in validations:
            validations[r_url] = r_doc.get("route_validation", {
                "status": "WORKING", "score": 100, "expected_url": r_url,
                "actual_url": r_url, "dom_stable": True, "page_load_success": True,
            })
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
    parking_lot = crawl_results.get("parking_lot_actions", [])
    scan_events = crawl_results.get("scan_events", [])

    test_cases_data = crawl_results.get("test_cases", [])
    if isinstance(test_cases_data, list) and test_cases_data:
        test_case_rows = []
        for tc in test_cases_data:
            if isinstance(tc, dict):
                test_case_rows.append({
                    "tc_id": tc.get("tc_id", ""),
                    "Testcase Id": tc.get("tc_id", ""),
                    "Screen": tc.get("screen") or tc.get("module", ""),
                    "Feature": tc.get("feature") or tc.get("module", ""),
                    "Testcase": tc.get("testcase") or tc.get("title", ""),
                    "Steps to Execute": tc.get("steps_to_execute") or "\n".join(str(s.get("action", "")) for s in tc.get("steps", []) if isinstance(s, dict)),
                    "Expected Result": "\n".join(str(s.get("expected_result", "")) for s in tc.get("steps", []) if isinstance(s, dict)),
                    "Test Status": tc.get("test_status") or tc.get("latest_result", "Not Executed"),
                    "Actual Result": tc.get("actual_result", ""),
                    "Severity": tc.get("severity", "Medium"),
                    "BUG Status": tc.get("bug_status", "Not Raised"),
                    "Notes / Reason": tc.get("notes_reason", ""),
                    "Bug sheet Current Version": tc.get("bug_sheet_current_version", "1"),
                    "Tested Version": tc.get("tested_version", ""),
                    "title": tc.get("title", ""),
                    "module": tc.get("module", ""),
                    "route": tc.get("route", ""),
                    "purpose": tc.get("purpose", ""),
                    "steps_count": len(tc.get("steps", [])),
                    "requirements": ", ".join(tc.get("requirement_references", [])),
                })
        tc_df = pd.DataFrame(test_case_rows)
    else:
        tc_df = pd.DataFrame(columns=["tc_id", "title", "module", "route", "purpose", "steps_count", "requirements"])

    exec_results_data = crawl_results.get("execution_results", [])
    if isinstance(exec_results_data, list) and exec_results_data:
        exec_rows = []
        for er in exec_results_data:
            if isinstance(er, dict):
                exec_rows.append({
                    "TC_ID": er.get("TC_ID", ""),
                    "Step_ID": er.get("Step_ID", ""),
                    "Action": er.get("Action", ""),
                    "element_identity": er.get("element_identity", ""),
                    "Screen": er.get("Screen", ""),
                    "Module": er.get("Module", ""),
                    "Test_Case": er.get("Test_Case", ""),
                    "Status": er.get("Status", ""),
                    "Locator_Used": er.get("locator_used", ""),
                    "Actual_Result": er.get("Actual_Result", ""),
                    "Failure_Info": er.get("Failure_Info", ""),
                    "Artifact_Path": er.get("Artifact_Path", ""),
                })
        exec_df = pd.DataFrame(exec_rows)
    else:
        exec_df = pd.DataFrame(columns=["TC_ID", "Step_ID", "Action", "element_identity", "Screen", "Module", "Test_Case", "Status", "Locator_Used", "Actual_Result", "Failure_Info", "Artifact_Path"])

    coverage_gaps_data = crawl_results.get("coverage_gaps", [])
    if isinstance(coverage_gaps_data, list) and coverage_gaps_data:
        cg_df = pd.DataFrame(coverage_gaps_data)
    else:
        cg_df = pd.DataFrame(columns=["gap_id", "module", "route", "feature", "reason", "recommendation"])

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        all_routes.to_excel(writer, sheet_name="Route Health", index=False)
        for status_check, sheet in (
            (is_working_route, "working_routes"),
            (is_partial_route, "partial_routes"),
            (is_non_working_route, "non_working_routes"),
        ):
            status_col = all_routes.get("status", pd.Series(dtype=str))
            mask = status_col.apply(lambda s: bool(status_check(str(s))))
            all_routes[mask].to_excel(
                writer, sheet_name=sheet, index=False
            )
        pd.DataFrame(hierarchy).to_excel(writer, sheet_name="menu_hierarchy", index=False)
        pd.DataFrame(elements).to_excel(writer, sheet_name="element_map", index=False)
        pd.DataFrame(parking_lot).to_excel(writer, sheet_name="parking_lot_actions", index=False)
        pd.DataFrame(scan_events).to_excel(writer, sheet_name="scan_events", index=False)
        tc_df.to_excel(writer, sheet_name="test_cases", index=False)
        exec_df.to_excel(writer, sheet_name="Test Execution", index=False)
        exec_df.to_excel(writer, sheet_name="execution_results", index=False)
        cg_df.to_excel(writer, sheet_name="coverage_gaps", index=False)


def _export_markdown(
    crawl_results: dict[str, Any], elements: list[dict[str, Any]], path: Path
) -> None:
    """Generate human-readable scan summary and gap analysis."""
    validations = dict(crawl_results.get("route_validations", {}) or {})
    page_intel = crawl_results.get("page_intelligence", {}) or {}
    for r_url, r_doc in page_intel.items():
        if r_url not in validations:
            validations[r_url] = r_doc.get("route_validation", {
                "status": "WORKING", "score": 100, "expected_url": r_url,
                "actual_url": r_url, "dom_stable": True, "page_load_success": True,
            })
    total_routes = len(validations)
    working_count = sum(1 for v in validations.values() if is_working_route(v.get("status")))
    partial_count = sum(1 for v in validations.values() if is_partial_route(v.get("status")))
    non_working_count = sum(1 for v in validations.values() if is_non_working_route(v.get("status")))
    parking_lot = crawl_results.get("parking_lot_actions", [])

    lines = [
        "# PiOne Testmate - Scan Summary",
        "",
        f"**Route Namespace**: `{crawl_results.get('route_namespace', 'N/A')}`  ",
        f"**Total Discovered Routes**: {total_routes}  ",
        f"**Working Routes**: {working_count}  ",
        f"**Partial Routes**: {partial_count}  ",
        f"**Non-Working Routes**: {non_working_count}  ",
        f"**Total Discovered Elements**: {len(elements)}  ",
        f"**Parking Lot (Manual Review Required)**: {len(parking_lot)}  ",
        "",
        "## Route Health Breakdown",
        "",
        "| Route | Status | Score | Elements | DOM Stable | Failed APIs | Redirected |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for route, val in sorted(validations.items()):
        status = val.get("status", "UNKNOWN")
        score = val.get("score", 0)
        t_count = val.get("target_count", 0)
        stable = "✅ Yes" if val.get("dom_stable") else "❌ No"
        failed_apis = val.get("failed_api_responses", 0)
        redirected = "⚠️ Yes" if val.get("redirected") else "No"
        lines.append(f"| `{route}` | **{status}** | {score} | {t_count} | {stable} | {failed_apis} | {redirected} |")

    if parking_lot:
        lines.extend([
            "",
            "## Parking Lot Actions (Requires User Review)",
            "",
            "| Route | Label | Classification | Reason |",
            "| :--- | :--- | :--- | :--- |",
        ])
        for action in parking_lot:
            r = action.get("route", "")
            lbl = action.get("label", "unlabeled")
            cls = action.get("classification", "")
            reason = action.get("reason", "")
            lines.append(f"| `{r}` | {lbl} | `{cls}` | {reason} |")

    exec_results = crawl_results.get("execution_results", [])
    if exec_results:
        lines.extend([
            "",
            "## Executed Test Actions",
            "",
            "| TC ID | Step | Action | Status | Locator Used | Actual Result |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for er in exec_results:
            tc_id = er.get("TC_ID", "")
            step_id = er.get("Step_ID", "")
            action = er.get("Action", "")
            status = er.get("Status", "")
            loc = (er.get("locator_used") or "").replace("|", "/")
            res = (er.get("Actual_Result") or "").replace("|", "/")
            lines.append(f"| `{tc_id}` | `{step_id}` | `{action}` | **{status}** | `{loc}` | {res} |")

    path.write_text("\n".join(lines), encoding="utf-8")


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

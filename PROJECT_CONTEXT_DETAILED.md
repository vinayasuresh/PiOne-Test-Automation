# PiOne Test Automation — Detailed Project Context

This document consolidates:

1. The overall project summary and architecture context,
2. A module-by-module call graph (who calls whom),
3. A practical "where to change what" map for future development.

---

## 1) Project summary (what this project is)

This repository is an **enterprise UI automation intelligence platform** built around **Python + Playwright** for scanning web applications and producing compact, automation-ready outputs.

It can:

- Crawl authenticated application routes,
- Detect meaningful UI components (buttons, inputs, dropdowns, tables, charts, maps, etc.),
- Capture optional interactive/manual user behavior,
- Export normalized JSON/YAML intelligence artifacts,
- Expose the workflow through a FastAPI backend and a browser-based frontend:
  - **Scan -> Analyze -> Generate Test -> Execute Test -> Report -> Export**

Primary references:

- [README.md](./README.md)
- [main.py](./main.py)
- [api/main.py](./api/main.py)
- [frontend/index.html](./frontend/index.html)

---

## 2) High-level architecture

Core framework package:

- [automation_framework/](./automation_framework/)

Key areas:

- **Config**
  - [automation_framework/config/settings.py](./automation_framework/config/settings.py)
- **Crawler orchestration**
  - [automation_framework/crawler/crawler_engine.py](./automation_framework/crawler/crawler_engine.py)
- **UI intelligence extraction**
  - [automation_framework/engine/ui_intelligence_engine.py](./automation_framework/engine/ui_intelligence_engine.py)
- **Output/export**
  - [automation_framework/crawler/crawl_exporter.py](./automation_framework/crawler/crawl_exporter.py)
  - [automation_framework/storage/json_storage.py](./automation_framework/storage/json_storage.py)
  - [automation_framework/storage/yaml_storage.py](./automation_framework/storage/yaml_storage.py)
- **Login/navigation/stability helpers**
  - [automation_framework/crawler/login_handler.py](./automation_framework/crawler/login_handler.py)
  - [automation_framework/crawler/menu_detector.py](./automation_framework/crawler/menu_detector.py)
  - [automation_framework/crawler/navigation_expander.py](./automation_framework/crawler/navigation_expander.py)
  - [automation_framework/crawler/shell_detector.py](./automation_framework/crawler/shell_detector.py)
  - [automation_framework/crawler/ui_wait_engine.py](./automation_framework/crawler/ui_wait_engine.py)
  - [automation_framework/crawler/hidden_navigation_explorer.py](./automation_framework/crawler/hidden_navigation_explorer.py)
  - [automation_framework/crawler/route_tracker.py](./automation_framework/crawler/route_tracker.py)
  - [automation_framework/crawler/url_filter.py](./automation_framework/crawler/url_filter.py)
- **Interactive learning capture**
  - [automation_framework/crawler/interactive_capture.py](./automation_framework/crawler/interactive_capture.py)
  - [automation_framework/crawler/interaction_recorder.py](./automation_framework/crawler/interaction_recorder.py)
- **Metadata/logging/browser lifecycle**
  - [automation_framework/utils/browser_manager.py](./automation_framework/utils/browser_manager.py)
  - [automation_framework/utils/logger.py](./automation_framework/utils/logger.py)
  - [automation_framework/utils/metadata_collector.py](./automation_framework/utils/metadata_collector.py)

API layer:

- [api/main.py](./api/main.py)
- [api/routers/scan.py](./api/routers/scan.py)
- [api/routers/scan_events.py](./api/routers/scan_events.py)
- [api/services/crawler_service.py](./api/services/crawler_service.py)
- [api/services/mock_service.py](./api/services/mock_service.py)
- [api/models/scan_models.py](./api/models/scan_models.py)
- [api/utils/response_builder.py](./api/utils/response_builder.py)

Frontend:

- [frontend/index.html](./frontend/index.html)
- [frontend/js/scan_status.js](./frontend/js/scan_status.js)

Schema documentation:

- [automation_framework/docs/output_schema.md](./automation_framework/docs/output_schema.md)

---

## 3) Runtime workflows

### 3.1 CLI workflow

Entrypoint: [main.py](./main.py)

Flow:

1. Prompt URL + username + password,
2. Start persistent browser context ([browser_manager.py](./automation_framework/utils/browser_manager.py)),
3. Login ([login_handler.py](./automation_framework/crawler/login_handler.py)),
4. Expand navigation if needed ([navigation_expander.py](./automation_framework/crawler/navigation_expander.py)),
5. Crawl ([crawler_engine.py](./automation_framework/crawler/crawler_engine.py)),
6. Optional assisted manual interactions ([interaction_recorder.py](./automation_framework/crawler/interaction_recorder.py)),
7. Export reports ([crawl_exporter.py](./automation_framework/crawler/crawl_exporter.py)),
8. Save metadata/log path ([metadata_collector.py](./automation_framework/utils/metadata_collector.py)).

### 3.2 API + UI workflow

Backend entry: [api/main.py](./api/main.py)

Frontend entry: [frontend/index.html](./frontend/index.html)

Primary endpoints in [api/routers/scan.py](./api/routers/scan.py):

- `POST /scan`
- `POST /generate-test`
- `POST /execute-test`
- `GET /system-info`

WebSocket endpoint:

- [api/routers/scan_events.py](./api/routers/scan_events.py) (`/ws/scan-events`, currently heartbeat-oriented).

---

## 4) Data and output model

Canonical schema contract: [automation_framework/docs/output_schema.md](./automation_framework/docs/output_schema.md)

Main output concepts:

- `routes`
- `component_registry`
- `manual_interactions`
- `interaction_flow`

API response adaptation for frontend:

- [api/utils/response_builder.py](./api/utils/response_builder.py) converts raw crawler output into compact `ScanResponse`.
- [api/models/scan_models.py](./api/models/scan_models.py) defines request/response contracts.

Artifact destinations:

- Reports: [automation_framework/reports/](./automation_framework/reports/)
- Logs: [automation_framework/logs/](./automation_framework/logs/)
- Session/auth state: [automation_framework/storage/auth_state.json](./automation_framework/storage/auth_state.json)
- Scan archives: [scans/](./scans/)

---

## 5) Module-by-module call graph (who calls whom)

This section maps execution paths in practical terms.

### 5.1 CLI graph

`main.py`
-> `generate_run_id`, `configure_run_logger`
-> `start_persistent_browser`
-> `login`
-> `ensure_navigation_expanded`
-> instantiate `CrawlerEngine(...)`
-> `CrawlerEngine.crawl(...)`
-> optional `record_manual_interactions`
-> `export_crawl_results`
-> `collect_metadata` + `save_metadata`

### 5.2 API /scan graph

`api/main.py`
-> registers routers from `api/routers/scan.py` and `api/routers/scan_events.py`

`POST /scan` in `api/routers/scan.py`
-> if mock mode: `mock_service.get_mock_response`
-> else: `asyncio.to_thread(crawler_service.run_scan, ...)`

`crawler_service.run_scan`
-> `generate_run_id`, `configure_run_logger`
-> `start_persistent_browser`
-> login branch:
   - credentials present -> `login`
   - else -> direct `page.goto`
-> `ensure_navigation_expanded` (best effort)
-> instantiate `CrawlerEngine(...)`
-> `CrawlerEngine.crawl(...)`
-> on success: `build_scan_response(...)`
-> on crawl exception: `_collect_partial(...)` + `build_scan_response(status="partial_success")`
-> export attempt: `export_crawl_results(...)`
-> always close context/browser/playwright in `finally`

### 5.3 Crawler engine graph

`CrawlerEngine.crawl`
-> `_ensure_authenticated`
-> `wait_for_ui_stability`
-> `detect_application_shell`
   -> `detect_menus`
-> `_scan_route(landing page)`
   -> `_stabilize_page`
   -> `build_ui_intelligence`
   -> optional `_capture_interactions`
      -> `inject_capture`
      -> `wait_for_user`
      -> `extract_events`
      -> `events_to_interactions`
      -> `interactions_to_flow`
      -> `interactions_to_targets`
   -> optional `_explore_hidden_state`
      -> `explore_hidden_navigation`
      -> callback may re-enter `_scan_route` or merge additional `build_ui_intelligence` output
-> `_build_menu_list`
   -> uses `is_valid_url`
-> for each menu item: `_visit_menu_item`
   -> `_click_menu_item` or `_navigate`
   -> `_ensure_authenticated`
   -> `wait_for_ui_stability`
   -> `_scan_route(current url)`
-> returns aggregate:
   - `page_intelligence`
   - `manual_interactions`
   - `interaction_flow`
   - `component_registry` via `build_component_registry`
   - `feature_routes`
   - visited route list from `RouteTracker`

### 5.4 UI intelligence graph

`build_ui_intelligence`
-> loops through `CATEGORY_ORDER` + `CATEGORY_RULES`
-> batched per-category extraction with `evaluate_all(...)`
-> `_is_meaningful_from_data`
-> `_build_target_from_data`
-> dedup (per-route + global)
-> optional `_probe_dropdown_behavior`
-> `_build_sections`
-> returns route doc with `automation_targets`

`build_component_registry`
-> flattens `automation_targets` across routes by category.

### 5.5 Export/metadata graph

`export_crawl_results`
-> `_normalize_route`
-> `_flatten_elements`
-> `save_json` / `save_yaml`

`collect_metadata`
-> summarizes runtime and scan metrics
-> `save_metadata` writes JSON metadata file.

### 5.6 Frontend/API graph

`frontend/index.html` script
-> `callAPI("/scan", ...)`
-> renders scan status + metrics + routes + components
-> `callAPI("/generate-test", ...)`
-> `callAPI("/execute-test", ...)`
-> fetches `/system-info`
-> local export helpers: JSON, YAML, XLS download.

`frontend/js/scan_status.js`
-> opens websocket to `/ws/scan-events`
-> updates counters/toasts based on received event type.

---

## 6) Where-to-change-what map (developer guide)

Use this as a quick maintenance map.

### 6.1 Change login behavior or improve auth resiliency

Edit:

- [automation_framework/crawler/login_handler.py](./automation_framework/crawler/login_handler.py)
- (auth session persistence path) [automation_framework/config/settings.py](./automation_framework/config/settings.py)

For API login handling expectations:

- [api/services/crawler_service.py](./api/services/crawler_service.py)
- [api/routers/scan.py](./api/routers/scan.py)

### 6.2 Adjust route discovery, dedup, or crawl traversal

Edit:

- [automation_framework/crawler/crawler_engine.py](./automation_framework/crawler/crawler_engine.py)
- [automation_framework/crawler/menu_detector.py](./automation_framework/crawler/menu_detector.py)
- [automation_framework/crawler/route_tracker.py](./automation_framework/crawler/route_tracker.py)
- [automation_framework/crawler/url_filter.py](./automation_framework/crawler/url_filter.py)

### 6.3 Tune stability/wait strategy for slow SPAs

Edit:

- [automation_framework/crawler/ui_wait_engine.py](./automation_framework/crawler/ui_wait_engine.py)
- [automation_framework/crawler/crawler_engine.py](./automation_framework/crawler/crawler_engine.py) (`_stabilize_page`)
- baseline timeout constants in [automation_framework/config/settings.py](./automation_framework/config/settings.py)

### 6.4 Change what UI elements are detected

Edit:

- [automation_framework/engine/ui_intelligence_engine.py](./automation_framework/engine/ui_intelligence_engine.py)
  - `CATEGORY_RULES`
  - `_is_meaningful_from_data`
  - selector/label/purpose/interaction classification helpers

If API response bucketing must match:

- [api/utils/response_builder.py](./api/utils/response_builder.py)

### 6.5 Change interactive learning capture format

Edit:

- [automation_framework/crawler/interactive_capture.py](./automation_framework/crawler/interactive_capture.py)
- [automation_framework/crawler/crawler_engine.py](./automation_framework/crawler/crawler_engine.py) (`_capture_interactions`)

Legacy/manual recorder behavior:

- [automation_framework/crawler/interaction_recorder.py](./automation_framework/crawler/interaction_recorder.py)

### 6.6 Change deep exploration of hidden navigation

Edit:

- [automation_framework/crawler/hidden_navigation_explorer.py](./automation_framework/crawler/hidden_navigation_explorer.py)
- feature flags/depth in [automation_framework/config/settings.py](./automation_framework/config/settings.py)
- merge strategy in [automation_framework/crawler/crawler_engine.py](./automation_framework/crawler/crawler_engine.py) (`_explore_hidden_state`)

### 6.7 Change API payload contracts

Edit:

- [api/models/scan_models.py](./api/models/scan_models.py) (request/response models)
- [api/utils/response_builder.py](./api/utils/response_builder.py) (shape conversion)
- [api/routers/scan.py](./api/routers/scan.py) (endpoint semantics)

Also update schema docs:

- [automation_framework/docs/output_schema.md](./automation_framework/docs/output_schema.md)

### 6.8 Change test generation logic

Edit:

- [api/routers/scan.py](./api/routers/scan.py)
  - `_build_pytest_script(...)`
  - `_build_execution_rows(...)`
  - `_feature_name(...)`

### 6.9 Change frontend workflow UX/steps/export

Edit:

- [frontend/index.html](./frontend/index.html) (all primary UI logic)
- [frontend/js/scan_status.js](./frontend/js/scan_status.js) (websocket status UX)

### 6.10 Change file outputs and naming conventions

Edit:

- [automation_framework/crawler/crawl_exporter.py](./automation_framework/crawler/crawl_exporter.py)
- [automation_framework/storage/json_storage.py](./automation_framework/storage/json_storage.py)
- [automation_framework/storage/yaml_storage.py](./automation_framework/storage/yaml_storage.py)
- run metadata in [automation_framework/utils/metadata_collector.py](./automation_framework/utils/metadata_collector.py)

### 6.11 Change browser lifecycle/headless mode/session state handling

Edit:

- [automation_framework/utils/browser_manager.py](./automation_framework/utils/browser_manager.py)
- [automation_framework/config/settings.py](./automation_framework/config/settings.py)

### 6.12 Change logging and observability

Edit:

- [automation_framework/utils/logger.py](./automation_framework/utils/logger.py)
- log callsites in crawler/API modules

---

## 7) Notable current state observations

- The repository includes substantial historical run artifacts in:
  - [automation_framework/reports/](./automation_framework/reports/)
  - [automation_framework/logs/](./automation_framework/logs/)
  - [scans/](./scans/)
- Pytest config exists in [pytest.ini](./pytest.ini), but [automation_framework/tests/](./automation_framework/tests/) currently appears minimal/empty.
- [erdataanalysis.py](./erdataanalysis.py) looks like a separate/legacy script and not part of the main framework + API flow.

---

## 8) Quick orientation for new contributors

If you need the fastest orientation path:

1. Read [README.md](./README.md) and [automation_framework/docs/output_schema.md](./automation_framework/docs/output_schema.md),
2. Follow execution in [main.py](./main.py) and [api/routers/scan.py](./api/routers/scan.py),
3. Deep dive in [automation_framework/crawler/crawler_engine.py](./automation_framework/crawler/crawler_engine.py),
4. Understand extraction rules in [automation_framework/engine/ui_intelligence_engine.py](./automation_framework/engine/ui_intelligence_engine.py),
5. Validate final payload shaping in [api/utils/response_builder.py](./api/utils/response_builder.py),
6. Inspect frontend orchestration in [frontend/index.html](./frontend/index.html).


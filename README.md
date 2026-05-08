# Automation Framework

Enterprise-focused Python Playwright framework for compact UI automation intelligence.

The framework is designed to understand application routes and extract only automation-relevant UI structure. It does not export raw DOM trees or wrapper-heavy page dumps.

## Current Architecture

```text
automation_framework/
    config/
        settings.py
    crawler/
        crawl_exporter.py
        crawler_engine.py
        interaction_recorder.py
        login_handler.py
        menu_detector.py
        navigation_expander.py
        route_tracker.py
        shell_detector.py
        ui_wait_engine.py
        url_filter.py
    engine/
        ui_intelligence_engine.py
    storage/
        json_storage.py
        yaml_storage.py
    utils/
        browser_manager.py
        logger.py
    reports/
    logs/
    screenshots/
    tests/
    docs/

main.py
pytest.ini
requirements.txt
```

## Active Pipeline

```text
login
-> stabilize visible UI
-> detect application shell
-> discover feature routes
-> scan meaningful automation targets
-> filter low-value elements
-> deduplicate targets
-> export compact JSON/YAML intelligence
```

## Output Model

Reports focus on:

- `application_shell`
- `feature_routes`
- `page_intelligence`
- `manual_interactions`
- `component_registry`
- `contextual_route_hierarchy`

Each page model keeps meaningful automation categories only:

- filters
- tables
- forms
- charts
- maps
- actions
- navigation
- dialogs
- tabs
- analytics
- validation_targets

## Design Rules

- Quality over quantity.
- Meaningful automation targets only.
- No raw DOM dumps.
- No wrapper div serialization.
- No hidden/decorative elements.
- Stable selector preference: `data-testid`, `aria-label`, role/name, stable ID, semantic label.
- Runtime credentials are entered securely with `getpass`.
- Logging is centralized through Loguru.

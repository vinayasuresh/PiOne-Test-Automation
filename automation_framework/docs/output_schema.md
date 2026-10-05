# UI Intelligence Output Schema (v1.0)

This document defines the **stable, automation-focused contract** produced by the
framework. Downstream automation code should bind to this schema only.

The schema is intentionally compact. Raw DOM, wrapper elements, hidden/decorative
nodes, and `LOW` relevance components are never exported.

---

## Top-level document

```jsonc
{
  "schema_version": "1.0",
  "routes": [ <Route>, ... ],
  "component_registry": { "<category>": [ <ComponentRef>, ... ] },
  "manual_interactions": [ ... ],
  "interaction_flow": [ <InteractionStep>, ... ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `schema_version` | string | Semantic version of this schema. Bump on breaking changes. |
| `routes` | array of `Route` | One entry per crawled route. Always normalized. |
| `component_registry` | object | Components grouped by category across all routes. |
| `manual_interactions` | array | Optional assisted-recorder captures. May be empty. |
| `interaction_flow` | array | Test-ready user action sequence captured during interactive learning. |

---

## Route

```jsonc
{
  "route": "https://app.example.com/orders",
  "menu_name": "Orders",
  "framework": "angular",
  "purpose": "Understand and validate orders",
  "sections": [
    { "name": "filters",  "count": 3 },
    { "name": "tables",   "count": 1 },
    { "name": "actions",  "count": 5 }
  ],
  "auto_detected_elements": [ <AutomationTarget>, ... ],
  "user_confirmed_elements": [ <AutomationTarget>, ... ],
  "automation_targets": [ <AutomationTarget>, ... ],
  "interaction_flow": [ <InteractionStep>, ... ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `route` | string | Absolute, normalized URL. |
| `menu_name` | string | Source menu label that led to this route (or `"home"` for the landing page). |
| `framework` | string | Detected frontend framework, currently `"angular"` or `"unknown"`. |
| `purpose` | string | One-line human-readable purpose. |
| `sections` | array of `Section` | Categories present on this route, in declaration order. |
| `auto_detected_elements` | array of `AutomationTarget` | Passive DOM/behavior scan results before user confirmation. |
| `user_confirmed_elements` | array of `AutomationTarget` | Elements explicitly marked by the user in interactive learning mode. |
| `automation_targets` | array of `AutomationTarget` | Final merged list: auto-detected plus user-confirmed elements. |
| `interaction_flow` | array of `InteractionStep` | Route-scoped recorded action sequence. |

Every route follows this shape. Empty sections are omitted; missing keys are not.

---

## Section

```jsonc
{ "name": "filters", "count": 3 }
```

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | One of: `filters`, `tables`, `forms`, `dropdowns`, `charts`, `maps`, `actions`, `inputs`, `interactive`, `navigation`, `dialogs`, `tabs`, `analytics`. |
| `count` | int | Number of components in this section on this route. |

---

## AutomationTarget (Component)

```jsonc
{
  "id": "filters::Status",
  "category": "filters",
  "type": "dropdown",
  "label": "Status",
  "purpose": "Filter data by Status",
  "interaction_type": "filter",
  "relevance": "HIGH",
  "selector": <Selector>,
  "framework": "angular",
  "framework_type": "mat-select",
  "component_tag": "mat-select",
  "is_dynamic": true,
  "is_validation_target": true
}
```

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Stable per-route identifier `<category>::<label-or-selector>`. |
| `category` | string | One of the allowed `Section.name` values. |
| `type` | string | Behavior classification: `button`, `input`, `dropdown`, `table`, `interactive`, or `unknown`. |
| `label` | string | Human-visible label (truncated to 80 chars). |
| `purpose` | string | What the component is used for. |
| `interaction_type` | string | `filter`, `click`, `input`, `select`, `interact`, `submit_form`, `navigate`, `validate_table`, `validate_visualization`, `validate_map`, `validate_dialog`, `validate_metric`, `switch_tab`. |
| `relevance` | string | `HIGH` or `MEDIUM`. `LOW` is filtered out. |
| `selector` | `Selector` | Stable locator block, see below. |
| `framework` | string | Framework context for this element. |
| `framework_type` | string | Native/framework component type, such as `mat-select`, `mat-button`, `app-button`, or `native`. |
| `component_tag` | string | Lowercase DOM tag name used by the component. |
| `is_dynamic` | bool | True when the element came from a framework/custom component or dynamic app root. |
| `is_validation_target` | bool | True when the component represents data to assert against. |

A **validation target** is the same shape as an `AutomationTarget`; the
`is_validation_target` flag identifies it. Categories `tables`, `forms`,
`charts`, `maps`, `analytics`, `dialogs`, `filters` are validation targets.

---

## Selector

```jsonc
{
  "primary": { "strategy": "testid", "value": "[data-testid=\"orders-status\"]" },
  "fallback": [
    "[aria-label=\"Status\"]",
    "role=combobox[name=\"Status\"]"
  ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `primary.strategy` | string | One of `testid`, `aria`, `role`, `id`. |
| `primary.value` | string | Locator string ready to use with Playwright. |
| `fallback` | array of string | Ordered alternative locators, may be empty. |

**Strategy priority** (most stable first): `testid` → `aria` → `role` → `id`.
A target without any usable selector is dropped.

---

## ComponentRef (used in `component_registry`)

```jsonc
{
  "route": "https://app.example.com/orders",
  "label": "Status",
  "purpose": "Filter data by Status",
  "interaction_type": "filter",
  "relevance": "HIGH",
  "selector": <Selector>
}
```

A flattened cross-route view, grouped by category at the top level.

---

## InteractionStep

```jsonc
{
  "step": "fill",
  "selector": "#email",
  "page": "https://app.example.com/login",
  "element_type": "input",
  "text": "Email",
  "value": "<dynamic>"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `step` | string | One of `click`, `fill`, or `select`. |
| `selector` | string | Test-ready CSS selector for the element. |
| `page` | string | Page URL where the interaction was captured. |
| `element_type` | string | Classified element type. |
| `text` | string | Human-visible label/text. |
| `value` | string | Always `"<dynamic>"` for fill/select so secrets are not stored. |

---

## Additional Export Files

Each crawl export writes:

- `<run_id>_crawl_results.json`
- `<run_id>_crawl_results.yaml`
- `<run_id>_crawl_results_routes.json`
- `<run_id>_crawl_results_elements.json`
- `<run_id>_crawl_results_interaction_flow.yaml`

---

## Guarantees

- Keys are **consistent across all routes**. No raw DOM, no wrapper IDs, no
  decorative attributes.
- Only `HIGH` and `MEDIUM` relevance components appear.
- Every component has a non-empty `selector.primary`.
- Every route has all four top-level keys: `route`, `purpose`, `sections`, `automation_targets`.

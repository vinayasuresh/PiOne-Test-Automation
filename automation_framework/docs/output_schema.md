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
  "manual_interactions": [ ... ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `schema_version` | string | Semantic version of this schema. Bump on breaking changes. |
| `routes` | array of `Route` | One entry per crawled route. Always normalized. |
| `component_registry` | object | Components grouped by category across all routes. |
| `manual_interactions` | array | Optional assisted-recorder captures. May be empty. |

---

## Route

```jsonc
{
  "route": "https://app.example.com/orders",
  "menu_name": "Orders",
  "purpose": "Understand and validate orders",
  "sections": [
    { "name": "filters",  "count": 3 },
    { "name": "tables",   "count": 1 },
    { "name": "actions",  "count": 5 }
  ],
  "automation_targets": [ <AutomationTarget>, ... ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `route` | string | Absolute, normalized URL. |
| `menu_name` | string | Source menu label that led to this route (or `"home"` for the landing page). |
| `purpose` | string | One-line human-readable purpose. |
| `sections` | array of `Section` | Categories present on this route, in declaration order. |
| `automation_targets` | array of `AutomationTarget` | Flat list of meaningful automation components. |

Every route follows this shape. Empty sections are omitted; missing keys are not.

---

## Section

```jsonc
{ "name": "filters", "count": 3 }
```

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | One of: `filters`, `tables`, `forms`, `charts`, `maps`, `actions`, `navigation`, `dialogs`, `tabs`, `analytics`. |
| `count` | int | Number of components in this section on this route. |

---

## AutomationTarget (Component)

```jsonc
{
  "id": "filters::Status",
  "category": "filters",
  "label": "Status",
  "purpose": "Filter data by Status",
  "interaction_type": "filter",
  "relevance": "HIGH",
  "selector": <Selector>,
  "is_validation_target": true
}
```

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Stable per-route identifier `<category>::<label-or-selector>`. |
| `category` | string | One of the allowed `Section.name` values. |
| `label` | string | Human-visible label (truncated to 80 chars). |
| `purpose` | string | What the component is used for. |
| `interaction_type` | string | `filter`, `click`, `submit_form`, `navigate`, `validate_table`, `validate_visualization`, `validate_map`, `validate_dialog`, `validate_metric`, `switch_tab`. |
| `relevance` | string | `HIGH` or `MEDIUM`. `LOW` is filtered out. |
| `selector` | `Selector` | Stable locator block, see below. |
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

## Guarantees

- Keys are **consistent across all routes**. No raw DOM, no wrapper IDs, no
  decorative attributes.
- Only `HIGH` and `MEDIUM` relevance components appear.
- Every component has a non-empty `selector.primary`.
- Every route has all four top-level keys: `route`, `purpose`, `sections`, `automation_targets`.

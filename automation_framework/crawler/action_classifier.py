"""ActionClassifier: Deterministic action safety and category classifier.

Classifies interactive controls into:
- safe_navigation: Links, breadcrumbs, back-to-list, internal navigation
- safe_expand: Menus, dropdowns, accordions, tabs, collapsible panels
- safe_filter: Filter chips, facets, reset filter
- safe_search: Search submit, query buttons
- safe_sort: Table column sorting headers, order toggles
- safe_pagination: Next, previous, page numbers, page size
- requires_manual_confirmation: Save, Submit, Update, Create, Edit, Checkout, Send, Approve, Reject
- blocked_destructive: Delete, Remove, Purge, Drop, Deactivate, Logout, Pay
- unknown: Unrecognized elements

Unsafe and manual-confirmation actions are diverted to the parking lot for user review.
"""

from __future__ import annotations

import re
from typing import Any


class ActionType:
    SAFE_NAVIGATION = "safe_navigation"
    SAFE_EXPAND = "safe_expand"
    SAFE_FILTER = "safe_filter"
    SAFE_SEARCH = "safe_search"
    SAFE_SORT = "safe_sort"
    SAFE_PAGINATION = "safe_pagination"
    REQUIRES_MANUAL_CONFIRMATION = "requires_manual_confirmation"
    BLOCKED_DESTRUCTIVE = "blocked_destructive"
    UNKNOWN = "unknown"


# Words that indicate immediate danger or permanent destruction/logout
DESTRUCTIVE_WORDS = {
    "delete", "remove", "drop", "purge", "terminate", "destroy", "wipe",
    "deactivate", "disable", "kill", "unlink", "format", "logout", "log out",
    "signout", "sign out", "disconnect", "exit", "pay", "purchase", "buy", "charge",
}

# Words that mutate application state or trigger real-world business transactions
CONFIRMATION_WORDS = {
    "save", "submit", "create", "add", "update", "edit", "modify", "publish",
    "post", "send", "approve", "reject", "confirm", "apply changes", "checkout",
    "order", "import", "upload", "overwrite", "commit", "enroll", "transfer",
}

# Words indicating safe, non-destructive navigation, view changes, or filtering
EXPAND_WORDS = {
    "expand", "collapse", "more", "toggle", "accordion", "menu",
    "dropdown", "show", "hide", "view more", "read more",
}

SEARCH_WORDS = {
    "search", "find", "query", "lookup", "clear search",
}

FILTER_WORDS = {
    "filter", "filters", "refine", "reset filters", "clear filters", "tag",
}

SORT_WORDS = {
    "sort", "order by", "ascending", "descending", "asc", "desc",
}

PAGINATION_WORDS = {
    "next", "previous", "prev", "page", "first", "last", "load more",
}

NAVIGATION_WORDS = {
    "view", "details", "back", "home", "navigate", "goto", "go to",
    "inspect", "explore", "profile", "overview", "read", "users",
    "dashboard", "reports", "settings",
}


def classify_action(
    element: dict[str, Any],
    custom_rules: dict[str, Any] | None = None,
) -> str:
    """Classify an interactive element into its deterministic ActionType."""
    label = (element.get("label") or element.get("text") or "").strip().lower()
    role = (element.get("role") or element.get("element_role") or "").strip().lower()
    tag = (element.get("tagName") or element.get("component_tag") or "").strip().lower()
    type_attr = (element.get("type") or "").strip().lower()
    aria_haspopup = str(element.get("aria_haspopup") or element.get("ariaHasPopup") or "").lower()
    aria_expanded = str(element.get("aria_expanded") or element.get("ariaExpanded") or "").lower()

    # 1. Check custom business rules first
    if custom_rules:
        blocked_custom = custom_rules.get("blocked_words", set())
        if any(word in label for word in blocked_custom):
            return ActionType.BLOCKED_DESTRUCTIVE
        manual_custom = custom_rules.get("manual_words", set())
        if any(word in label for word in manual_custom):
            return ActionType.REQUIRES_MANUAL_CONFIRMATION

    # 2. Blocked destructive actions
    if any(re.search(rf"\b{re.escape(word)}\b", label) for word in DESTRUCTIVE_WORDS):
        return ActionType.BLOCKED_DESTRUCTIVE

    # 3. Actions requiring manual user confirmation
    if any(re.search(rf"\b{re.escape(word)}\b", label) for word in CONFIRMATION_WORDS):
        return ActionType.REQUIRES_MANUAL_CONFIRMATION

    if type_attr == "submit" or role == "submit":
        return ActionType.REQUIRES_MANUAL_CONFIRMATION

    # 4. Safe expand / toggle / accordions / tabs
    if aria_expanded in {"true", "false"} or aria_haspopup in {"true", "menu", "listbox", "dialog"}:
        return ActionType.SAFE_EXPAND

    if (
        role in {"tab", "switch"}
        or "accordion" in tag
        or "mat-expansion-panel" in tag
        or tag == "summary"
        or element.get("is_accordion")
        or bool(element.get("aria_controls"))
        or "accordion" in element.get("classes", "").lower()
    ):
        return ActionType.SAFE_EXPAND

    if any(re.search(rf"\b{re.escape(word)}\b", label) for word in EXPAND_WORDS):
        return ActionType.SAFE_EXPAND

    # 5. Safe search
    if role == "search" or any(re.search(rf"\b{re.escape(word)}\b", label) for word in SEARCH_WORDS):
        return ActionType.SAFE_SEARCH

    # 6. Safe filter
    if any(re.search(rf"\b{re.escape(word)}\b", label) for word in FILTER_WORDS):
        return ActionType.SAFE_FILTER

    # 7. Safe sort
    if role == "columnheader" or any(re.search(rf"\b{re.escape(word)}\b", label) for word in SORT_WORDS):
        return ActionType.SAFE_SORT

    # 8. Safe pagination
    if (
        any(re.search(rf"\b{re.escape(word)}\b", label) for word in PAGINATION_WORDS)
        or element.get("is_pagination")
        or "page-link" in element.get("classes", "").lower()
        or "pagination" in element.get("classes", "").lower()
        or "p-paginator" in element.get("classes", "").lower()
        or (re.match(r"^\d+$", label) and (element.get("is_pagination") or "page" in element.get("classes", "").lower()))
    ):
        return ActionType.SAFE_PAGINATION

    # 9. Lazy-loaded menu items & safe dropdown options
    if role in {"menuitem", "option"} or tag in {"option", "mat-option"} or "dropdown-item" in element.get("classes", "").lower():
        if element.get("href") or element.get("routerlink") or element.get("data-route") or element.get("data-href"):
            return ActionType.SAFE_NAVIGATION
        return ActionType.SAFE_EXPAND

    # 10. Safe navigation (links, routerLinks, navigation buttons)
    if (
        tag == "a"
        or role == "link"
        or element.get("routerlink")
        or element.get("href")
        or element.get("data-route")
        or element.get("data-href")
        or any(re.search(rf"\b{re.escape(word)}\b", label) for word in NAVIGATION_WORDS)
    ):
        return ActionType.SAFE_NAVIGATION

    # Fallback for generic buttons or interactives
    if role == "button" or tag == "button":
        # Unlabeled or ambiguous buttons require review if they could mutate data
        return ActionType.UNKNOWN

    return ActionType.UNKNOWN


def is_safe_action(action_type: str) -> bool:
    """True if the action is read-only or safe navigation/expansion."""
    return action_type in {
        ActionType.SAFE_NAVIGATION,
        ActionType.SAFE_EXPAND,
        ActionType.SAFE_FILTER,
        ActionType.SAFE_SEARCH,
        ActionType.SAFE_SORT,
        ActionType.SAFE_PAGINATION,
    }


def is_parking_lot_action(action_type: str) -> bool:
    """True if action should be diverted to the parking lot for user review."""
    return action_type in {
        ActionType.REQUIRES_MANUAL_CONFIRMATION,
        ActionType.BLOCKED_DESTRUCTIVE,
        ActionType.UNKNOWN,
    }

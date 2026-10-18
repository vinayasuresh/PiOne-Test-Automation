from automation_framework.crawler.action_classifier import (
    ActionType,
    classify_action,
    is_safe_action,
    is_parking_lot_action,
)

def test_classify_blocked_destructive():
    assert classify_action({"label": "Delete Account", "role": "button"}) == ActionType.BLOCKED_DESTRUCTIVE
    assert classify_action({"label": "Drop Table", "role": "button"}) == ActionType.BLOCKED_DESTRUCTIVE
    assert classify_action({"label": "Logout", "role": "button"}) == ActionType.BLOCKED_DESTRUCTIVE
    assert classify_action({"label": "Pay Now", "role": "button"}) == ActionType.BLOCKED_DESTRUCTIVE

def test_classify_requires_manual_confirmation():
    assert classify_action({"label": "Save Changes", "role": "button"}) == ActionType.REQUIRES_MANUAL_CONFIRMATION
    assert classify_action({"label": "Submit Application", "role": "button"}) == ActionType.REQUIRES_MANUAL_CONFIRMATION
    assert classify_action({"label": "Create User", "role": "button"}) == ActionType.REQUIRES_MANUAL_CONFIRMATION
    assert classify_action({"label": "Send Message", "role": "button"}) == ActionType.REQUIRES_MANUAL_CONFIRMATION
    assert classify_action({"label": "Approve Request", "role": "button"}) == ActionType.REQUIRES_MANUAL_CONFIRMATION

def test_classify_safe_actions():
    assert classify_action({"label": "Next Page", "role": "button"}) == ActionType.SAFE_PAGINATION
    assert classify_action({"label": "Sort by Date", "role": "button"}) == ActionType.SAFE_SORT
    assert classify_action({"label": "Filter Active", "role": "button"}) == ActionType.SAFE_FILTER
    assert classify_action({"label": "Search Documents", "role": "button"}) == ActionType.SAFE_SEARCH
    assert classify_action({"label": "Toggle Menu", "aria_expanded": "false", "role": "button"}) == ActionType.SAFE_EXPAND
    assert classify_action({"label": "Settings", "tagName": "a", "role": "link"}) == ActionType.SAFE_NAVIGATION

def test_safety_predicates():
    assert is_safe_action(ActionType.SAFE_NAVIGATION)
    assert is_safe_action(ActionType.SAFE_EXPAND)
    assert is_safe_action(ActionType.SAFE_FILTER)
    assert not is_safe_action(ActionType.BLOCKED_DESTRUCTIVE)
    assert not is_safe_action(ActionType.REQUIRES_MANUAL_CONFIRMATION)

    assert is_parking_lot_action(ActionType.BLOCKED_DESTRUCTIVE)
    assert is_parking_lot_action(ActionType.REQUIRES_MANUAL_CONFIRMATION)
    assert is_parking_lot_action(ActionType.UNKNOWN)
    assert not is_parking_lot_action(ActionType.SAFE_NAVIGATION)

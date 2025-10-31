# src/selectors/__init__.py
"""
Selectors package
-----------------
Helpers to translate workflow selectors into Playwright locators and
apply robust fallback logic across multiple strategies.
"""

from .locator import resolve_locator, wait_for_selector_state
from .strategy import LocatorStrategy, SelectorNotFound, ActionRetryError

__all__ = [
    "resolve_locator",
    "wait_for_selector_state",
    "LocatorStrategy",
    "SelectorNotFound",
    "ActionRetryError",
]

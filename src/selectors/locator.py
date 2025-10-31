# src/selectors/locator.py
from __future__ import annotations

from typing import Optional, Tuple

from playwright.sync_api import Locator, Page

from src.core.workflow_loader import Selector, SelectorStrategy
from src.utils.logger import get_logger

log = get_logger(__name__)


def _parse_role_value(value: str) -> Tuple[str, Optional[str]]:
    """
    Accept a few simple role notations for flexibility:

    - "button"                      → role="button"
    - "button|Create Project"       → role="button", name="Create Project"
    - "button name=Create Project"  → same as above (space syntax)
    - "textbox|Project name"        → role="textbox", name="Project name"

    Returns: (role, accessible_name_or_None)
    """
    v = value.strip()
    if "|" in v:
        role, name = v.split("|", 1)
        return role.strip(), name.strip() or None
    if " name=" in v:
        role, name = v.split(" name=", 1)
        return role.strip(), name.strip() or None
    return v, None


def resolve_locator(page: Page, sel: Selector) -> Locator:
    """
    Convert our schema Selector into a Playwright Locator.
    """
    strategy = sel.strategy
    value = sel.value

    if strategy == SelectorStrategy.css:
        return page.locator(value)

    if strategy == SelectorStrategy.text:
        # Use get_by_text with partial match by default; users can anchor with ^...$ in YAML if needed.
        return page.get_by_text(value, exact=False)

    if strategy == SelectorStrategy.role:
        role, name = _parse_role_value(value)
        kwargs = {}
        if name:
            kwargs["name"] = name
        try:
            return page.get_by_role(role=role, **kwargs)  # type: ignore[arg-type]
        except Exception:
            # get_by_role expects one of ARIA roles; if invalid, fall back to CSS search for role attr
            css = f'[role="{role}"]' + (f'[aria-label="{name}"]' if name else "")
            return page.locator(css)

    if strategy == SelectorStrategy.xpath:
        return page.locator(f"xpath={value}")

    # Fallback: treat as CSS
    log.debug(f"Unknown selector strategy '{strategy}', falling back to css for value={value!r}")
    return page.locator(value)


def wait_for_selector_state(
    page: Page,
    sel: Selector,
    *,
    state: str = "visible",
    timeout_ms: Optional[int] = None,
) -> Locator:
    """
    Wait for selector to reach a given state and return its Locator.
    state ∈ {"attached","detached","visible","hidden"}
    """
    loc = resolve_locator(page, sel)
    loc.wait_for(state=state, timeout=timeout_ms if timeout_ms is not None else sel.timeout_ms)
    return loc

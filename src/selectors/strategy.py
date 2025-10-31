# src/selectors/strategy.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from playwright.sync_api import Locator, Page

from src.core.workflow_loader import Selector
from src.selectors.locator import resolve_locator
from src.utils.logger import get_logger
from src.utils.timing import retry, sleep_ms

log = get_logger(__name__)


class SelectorNotFound(RuntimeError):
    pass


class ActionRetryError(RuntimeError):
    pass


@dataclass
class ChosenLocator:
    selector: Selector
    locator: Locator
    how: str  # "visible" | "first" | "state-wait"
    index: int


class LocatorStrategy:
    """
    Multi-selector fallback with small, opinionated heuristics:
      - Try selectors in order
      - Prefer the first *visible* locator
      - If none visible, take the first that exists and wait for visibility briefly
      - Optional retries with exponential backoff
    """

    def __init__(self, page: Page, *, per_selector_wait_ms: int = 2000) -> None:
        self.page = page
        self.per_selector_wait_ms = max(0, per_selector_wait_ms)

    # ---------- Selection ----------

    def choose(self, selectors: Sequence[Selector], *, require_visible: bool = True) -> ChosenLocator:
        errors: List[str] = []
        first_present: Optional[Tuple[int, Selector, Locator]] = None

        for idx, sel in enumerate(selectors):
            loc = resolve_locator(self.page, sel)
            try:
                # Fast existence check
                if loc.count() == 0:
                    errors.append(f"[{idx}] none found: {sel.strategy}:{sel.value}")
                    continue

                # Visible wins immediately
                if require_visible and loc.first.is_visible(timeout=self.per_selector_wait_ms):
                    return ChosenLocator(selector=sel, locator=loc.first, how="visible", index=idx)

                # otherwise remember that at least something matched
                if first_present is None:
                    first_present = (idx, sel, loc.first)
            except Exception as e:
                errors.append(f"[{idx}] error: {sel.strategy}:{sel.value} -> {e!r}")

        # If we saw a present one, try waiting a bit for it to become visible
        if first_present:
            idx, sel, loc = first_present
            try:
                loc.wait_for(state="visible", timeout=self.per_selector_wait_ms)
                return ChosenLocator(selector=sel, locator=loc, how="state-wait", index=idx)
            except Exception:
                pass

        raise SelectorNotFound(
            "No selector matched (or became visible). Tried:\n  " + "\n  ".join(errors or ["<none>"])
        )

    # ---------- Convenience Actions (with fallback) ----------

    def click(self, selectors: Sequence[Selector], *, retries: int = 2) -> ChosenLocator:
        def _do() -> ChosenLocator:
            chosen = self.choose(selectors, require_visible=True)
            chosen.locator.click()
            return chosen

        try:
            return retry(_do, tries=max(1, retries + 1))
        except Exception as e:
            raise ActionRetryError(f"click failed after retries: {e}") from e

    def fill(self, selectors: Sequence[Selector], text: str, *, clear: bool = True, retries: int = 1) -> ChosenLocator:
        def _do() -> ChosenLocator:
            chosen = self.choose(selectors, require_visible=True)
            if clear:
                chosen.locator.fill(text)
            else:
                chosen.locator.type(text)
            return chosen

        try:
            return retry(_do, tries=max(1, retries + 1))
        except Exception as e:
            raise ActionRetryError(f"fill/type failed after retries: {e}") from e

    def hover(self, selectors: Sequence[Selector], *, retries: int = 1) -> ChosenLocator:
        def _do() -> ChosenLocator:
            chosen = self.choose(selectors, require_visible=True)
            chosen.locator.hover()
            return chosen

        try:
            return retry(_do, tries=max(1, retries + 1))
        except Exception as e:
            raise ActionRetryError(f"hover failed after retries: {e}") from e

    def check(self, selectors: Sequence[Selector], *, should_check: bool = True, retries: int = 1) -> ChosenLocator:
        def _do() -> ChosenLocator:
            chosen = self.choose(selectors, require_visible=True)
            if should_check:
                chosen.locator.check()
            else:
                chosen.locator.uncheck()
            return chosen

        try:
            return retry(_do, tries=max(1, retries + 1))
        except Exception as e:
            raise ActionRetryError(f"check/uncheck failed after retries: {e}") from e

    def select_option(
        self,
        selectors: Sequence[Selector],
        *,
        value: Optional[str] = None,
        label: Optional[str] = None,
        index: Optional[int] = None,
        retries: int = 1,
    ) -> ChosenLocator:
        opts = {"value": value, "label": label, "index": index}

        def _do() -> ChosenLocator:
            chosen = self.choose(selectors, require_visible=True)
            chosen.locator.select_option({k: v for k, v in opts.items() if v is not None})
            return chosen

        try:
            return retry(_do, tries=max(1, retries + 1))
        except Exception as e:
            raise ActionRetryError(f"select_option failed after retries: {e}") from e

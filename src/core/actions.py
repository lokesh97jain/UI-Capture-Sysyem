# src/core/actions.py
from __future__ import annotations

"""Workflow actions dispatcher
------------------------------
Maps validated workflow steps to Playwright operations with retries and
centralized screenshot + metadata handling.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Any

from playwright.sync_api import Page, Locator, TimeoutError as PWTimeoutError

from src.utils.config import get_settings, Settings
from src.utils.logger import get_logger, log_with_context
from src.utils.timing import retry, measure
# ScreenshotManager centralizes page/element screenshots and optional masking
from src.capture.screenshot import ScreenshotManager
# MetadataBuilder writes per-image sidecars and appends a captures.jsonl stream
from src.capture.metadata import MetadataBuilder
from src.core.workflow_loader import (
    ActionName,
    Selector,
    StepClick,
    StepHover,
    StepType,
    StepFill,
    StepPress,
    StepWait,
    StepWaitForSelector,
    StepWaitForNetworkIdle,
    StepSelectOption,
    StepCheck,
    StepUncheck,
    StepSetInputFiles,
    StepScreenshot,
    StepGoto,
    StepAssertVisible,
    StepAssertText,
    StepAssertUrlContains,
)

# Public API
__all__ = ["execute_step"]


@dataclass
class RunContextLike:
    """Minimal shape of engine RunContext used for output paths and naming."""
    run_id: str
    site: str
    task: str
    output_dir: Path
    default_after_wait_ms: Optional[int]


# ------------- Internals -------------

def _resolve_locator(page: Page, sel: Selector) -> Locator:
    """
    Return a Locator based on selector.strategy.
    We prefer Playwright's built-in selector engines (css, text=, xpath=, role=).
    """
    if sel.strategy.value == "css":
        return page.locator(sel.value)
    if sel.strategy.value == "text":
        # Playwright's text engine
        return page.locator(f"text={sel.value}")
    if sel.strategy.value == "xpath":
        return page.locator(f"xpath={sel.value}")
    if sel.strategy.value == "role":
        # Playwright supports role= selectors, e.g. role=button[name="Create"]
        return page.locator(f"role={sel.value}")
    # Fallback to css
    return page.locator(sel.value)


def _with_retry(op: Callable[[], Any], *, tries: int, initial_delay_ms: int, max_delay_ms: int):
    """
    Wrap an operation with retry/backoff on common transient failures.
    """
    return retry(
        op,
        tries=max(1, tries),
        initial_delay_ms=initial_delay_ms,
        max_delay_ms=max_delay_ms,
        exceptions=(PWTimeoutError, Exception),
    )


def _get_output_dir(ctx: Any) -> Path:
    try:
        if hasattr(ctx, "output_dir") and getattr(ctx, "output_dir"):
            return Path(getattr(ctx, "output_dir"))
    except Exception:
        pass
    # Support dict-style run_ctx from Engine
    if isinstance(ctx, dict):
        p = ctx.get("output_dir") or ctx.get("images_dir") or ctx.get("run_dir")
        if p:
            return Path(p)
    return Path.cwd()


def _screenshot_path(ctx: Any, base_name: str, ext: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in base_name)
    return _get_output_dir(ctx) / f"{safe}.{ext}"


# ------------- Step executors -------------

@measure("goto")
def _do_goto(page: Page, step: StepGoto, settings: Settings):
    page.goto(step.url, wait_until="domcontentloaded", timeout=settings.PAGE_LOAD_TIMEOUT)


@measure("click")
def _do_click(page: Page, step: StepClick, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    loc.click(timeout=step.selector.timeout_ms)


@measure("hover")
def _do_hover(page: Page, step: StepHover, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    loc.hover(timeout=step.selector.timeout_ms)


@measure("type")
def _do_type(page: Page, step: StepType, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    if step.clear:
        loc.fill(step.text, timeout=step.selector.timeout_ms)
    else:
        loc.type(step.text, timeout=step.selector.timeout_ms)


@measure("fill")
def _do_fill(page: Page, step: StepFill, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    loc.fill(step.text, timeout=step.selector.timeout_ms)


@measure("press")
def _do_press(page: Page, step: StepPress, settings: Settings):
    if step.selector:
        loc = _resolve_locator(page, step.selector)
        loc.press(step.key, timeout=step.selector.timeout_ms)
    else:
        # Press on page (focused element)
        page.keyboard.press(step.key)


@measure("wait (static)")
def _do_wait(page: Page, step: StepWait, settings: Settings):
    page.wait_for_timeout(step.ms)


@measure("wait_for_selector")
def _do_wait_for_selector(page: Page, step: StepWaitForSelector, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    loc.wait_for(state=step.state, timeout=step.selector.timeout_ms)


@measure("wait_for_network_idle")
def _do_wait_for_network_idle(page: Page, step: StepWaitForNetworkIdle, settings: Settings):
    # Playwright's 'networkidle' load state ensures no network for ~500ms.
    page.wait_for_load_state("networkidle", timeout=max(settings.PAGE_LOAD_TIMEOUT, step.idle_ms + 1000))
    # Then honor extra idle time requested
    if step.idle_ms > 0:
        page.wait_for_timeout(step.idle_ms)


@measure("select_option")
def _do_select_option(page: Page, step: StepSelectOption, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    if step.value is not None:
        loc.select_option(value=step.value, timeout=step.selector.timeout_ms)
    elif step.label is not None:
        loc.select_option(label=step.label, timeout=step.selector.timeout_ms)
    else:
        # index must be not-None by validation if neither value/label
        loc.select_option(index=step.index, timeout=step.selector.timeout_ms)


@measure("check")
def _do_check(page: Page, step: StepCheck, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    loc.check(timeout=step.selector.timeout_ms)


@measure("uncheck")
def _do_uncheck(page: Page, step: StepUncheck, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    loc.uncheck(timeout=step.selector.timeout_ms)


@measure("set_input_files")
def _do_set_input_files(page: Page, step: StepSetInputFiles, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    files = [str(p) for p in step.files]
    loc.set_input_files(files, timeout=step.selector.timeout_ms)


@measure("screenshot")
def _do_screenshot(page: Page, step: StepScreenshot, settings: Settings, ctx: RunContextLike, log_name: str):
    """Take a screenshot and persist rich metadata.

    Primary path uses ScreenshotManager (for consistent naming and future masking) and
    MetadataBuilder to emit two artifacts:
      - <image>.<ext>.json sidecar next to the image
      - captures.jsonl (one JSON object per capture)

    If anything fails in the richer path (e.g., Pillow is missing), gracefully
    fall back to the simple Playwright screenshot used previously.
    """
    try:
        try:
            # run_dir can come from Engine's run_ctx dict or a typed context
            run_dir = Path(getattr(ctx, "output_dir", None) or ctx.get("run_dir") or ctx.get("images_dir") or Path.cwd())
        except Exception:
            run_dir = Path.cwd()
        shots = ScreenshotManager(run_dir)
        builder = MetadataBuilder(run_dir)
        name = step.name or log_name
        if step.selector:
            loc = _resolve_locator(page, step.selector)
            cap = shots.element(page, loc, name=name)
        else:
            cap = shots.page(page, name=name, full_page=(step.full_page if step.full_page is not None else settings.FULL_PAGE_SCREENSHOT))
        # record() writes the sidecar and appends to captures.jsonl
        builder.record(cap, step_index=getattr(step, "_index", None), step_action=step.action.value, site=None, task=None, extra={})
        return cap.path
    except Exception:
        # Fallback to original simple screenshot if advanced path fails
        fmt = settings.SCREENSHOT_FORMAT.value
        is_full = step.full_page if step.full_page is not None else settings.FULL_PAGE_SCREENSHOT
        out_path = _screenshot_path(ctx, step.name or log_name, fmt)
        if step.selector:
            loc = _resolve_locator(page, step.selector)
            loc.screenshot(path=str(out_path), type=fmt, quality=(settings.SCREENSHOT_QUALITY if fmt == "jpeg" else None))
        else:
            page.screenshot(path=str(out_path), type=fmt, full_page=is_full, quality=(settings.SCREENSHOT_QUALITY if fmt == "jpeg" else None))
        return out_path


@measure("assert_visible")
def _do_assert_visible(page: Page, step: StepAssertVisible, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    loc.wait_for(state=step.state, timeout=step.selector.timeout_ms)
    # nothing to return; raise on failure


@measure("assert_text")
def _do_assert_text(page: Page, step: StepAssertText, settings: Settings):
    loc = _resolve_locator(page, step.selector)
    loc.wait_for(state="visible", timeout=step.selector.timeout_ms)
    txt = loc.inner_text(timeout=step.selector.timeout_ms) or ""
    if step.regex:
        import re
        if not re.search(step.expect, txt, flags=re.IGNORECASE):
            raise AssertionError(f"assert_text failed: pattern {step.expect!r} not found in {txt!r}")
    else:
        if step.expect.lower() not in txt.lower():
            raise AssertionError(f"assert_text failed: '{step.expect}' not in {txt!r}")


@measure("assert_url_contains")
def _do_assert_url_contains(page: Page, step: StepAssertUrlContains, settings: Settings):
    url = page.url or ""
    if step.text.lower() not in url.lower():
        raise AssertionError(f"assert_url_contains failed: '{step.text}' not in URL {url}")


# ------------- Dispatcher -------------

def execute_step(page: Page, step: Any, run_ctx: RunContextLike) -> Optional[Path]:
    """
    Execute one validated step (click/type/goto/screenshot/etc.) on the page.
    Returns the saved artifact path, if any (e.g., screenshot), otherwise None.
    """
    settings = get_settings()
    log = get_logger(__name__)
    local_log = log_with_context(log, action=getattr(step, "action", None))

    # Global retry policy from settings
    tries = max(1, settings.MAX_RETRIES)
    initial_delay = max(0, settings.RETRY_DELAY)
    max_delay = max(initial_delay, 2000)

    def run(op: Callable[[], Any]):
        return _with_retry(op, tries=tries, initial_delay_ms=initial_delay, max_delay_ms=max_delay)

    action = step.action

    # ---- Dispatch on action type
    if action == ActionName.goto:
        return run(lambda: _do_goto(page, step, settings))
    elif action == ActionName.click:
        return run(lambda: _do_click(page, step, settings))
    elif action == ActionName.hover:
        return run(lambda: _do_hover(page, step, settings))
    elif action == ActionName.type:
        return run(lambda: _do_type(page, step, settings))
    elif action == ActionName.fill:
        return run(lambda: _do_fill(page, step, settings))
    elif action == ActionName.press:
        return run(lambda: _do_press(page, step, settings))
    elif action == ActionName.wait:
        return _do_wait(page, step, settings)
    elif action == ActionName.wait_for_selector:
        return run(lambda: _do_wait_for_selector(page, step, settings))
    elif action == ActionName.wait_for_network_idle:
        return run(lambda: _do_wait_for_network_idle(page, step, settings))
    elif action == ActionName.select_option:
        return run(lambda: _do_select_option(page, step, settings))
    elif action == ActionName.check:
        return run(lambda: _do_check(page, step, settings))
    elif action == ActionName.uncheck:
        return run(lambda: _do_uncheck(page, step, settings))
    elif action == ActionName.set_input_files:
        return run(lambda: _do_set_input_files(page, step, settings))
    elif action == ActionName.screenshot:
        # For screenshots, we also return the path (engine manifest will pick it up anyway)
        path = run(lambda: _do_screenshot(page, step, settings, run_ctx, log_name=step.name or step.action.value))
        local_log.info(f"Saved screenshot: {path}")
        return path
    elif action == ActionName.assert_visible:
        return run(lambda: _do_assert_visible(page, step, settings))
    elif action == ActionName.assert_text:
        return run(lambda: _do_assert_text(page, step, settings))
    elif action == ActionName.assert_url_contains:
        return run(lambda: _do_assert_url_contains(page, step, settings))
    else:
        raise NotImplementedError(f"Unsupported action: {action}")

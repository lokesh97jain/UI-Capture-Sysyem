# src/detection/stability.py
from __future__ import annotations

"""Page stability helpers
------------------------
Utility waits for network idle, DOM stability (MutationObserver), and finishing
animations to improve screenshot consistency.
"""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

from src.utils.config import get_settings
from src.utils.logger import get_logger
from src.utils.timing import now_ms, async_sleep_ms, sleep_ms, wait_for, measure


log = get_logger(__name__)


@measure("wait_for_network_idle")
def wait_for_network_idle(page: Page, timeout_ms: Optional[int] = None) -> None:
    """Playwright-level network idle (no requests for ~500ms)."""
    s = get_settings()
    page.wait_for_load_state(
        "networkidle",
        timeout=timeout_ms if timeout_ms is not None else s.PAGE_LOAD_TIMEOUT,
    )


@measure("wait_for_dom_stable")
def wait_for_dom_stable(page: Page, settle_ms: int = 300, max_wait_ms: Optional[int] = None) -> None:
    """
    Wait until DOM mutation rate drops to ~zero for `settle_ms` window.
    Uses a MutationObserver in the page to count mutations; we poll that counter.
    """
    s = get_settings()
    max_wait = max_wait_ms if max_wait_ms is not None else s.PAGE_LOAD_TIMEOUT

    # Initialize a counter once per page
    page.evaluate(
        """
        window.__uiCap = window.__uiCap || {};
        if (!window.__uiCap.domCounter) {
          window.__uiCap.domCounter = { n: 0, last: Date.now() };
          const obs = new MutationObserver(() => {
            window.__uiCap.domCounter.n++;
            window.__uiCap.domCounter.last = Date.now();
          });
          obs.observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true});
        }
        """
    )

    deadline = now_ms() + max_wait
    # We consider DOM "stable" if no mutations for 'settle_ms'
    while True:
        last = page.evaluate("() => (window.__uiCap?.domCounter?.last) || Date.now()")
        idle = max(0, int(now_ms() - int(last)))
        if idle >= settle_ms:
            # double-check with a paint cycle
            page.evaluate(
                """() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))"""
            )
            return
        if now_ms() >= deadline:
            log.debug(f"DOM stability timeout after {max_wait} ms (idle seen: {idle} ms)")
            return
        sleep_ms(min(100, max(10, settle_ms // 4)))


@measure("wait_for_animations_to_finish")
def wait_for_animations_to_finish(page: Page, selector: Optional[str] = None, min_idle_ms: int = 200) -> None:
    """
    Polls computed animation & transition state. Returns when nothing is running.
    Heuristics only—won't block forever if page lies about running animations.
    """
    script = """
    (sel, minIdle) => {
      const now = () => performance.now();
      const endTimes = [];

      const nodes = sel ? Array.from(document.querySelectorAll(sel)) : [document.documentElement];

      const consider = (el) => {
        const cs = getComputedStyle(el);
        // transitions
        const tDur = cs.transitionDuration.split(',').map(s => parseFloat(s) * 1000 || 0);
        const tDelay = cs.transitionDelay.split(',').map(s => parseFloat(s) * 1000 || 0);
        for (let i=0; i<Math.max(tDur.length, tDelay.length); i++) {
          const end = now() + (tDur[i] || 0) + (tDelay[i] || 0);
          if (end > now()) endTimes.push(end);
        }
        // animations
        const aDur = cs.animationDuration.split(',').map(s => parseFloat(s) * 1000 || 0);
        const aDelay = cs.animationDelay.split(',').map(s => parseFloat(s) * 1000 || 0);
        const aIter = cs.animationIterationCount.split(',').map(v => (v.trim()==='infinite' ? 1 : parseFloat(v)||0));
        for (let i=0; i<Math.max(aDur.length, aDelay.length, aIter.length); i++) {
          // heuristic: single-iteration max; we cannot predict infinite loops
          const iters = aIter[i] || 1;
          const end = now() + (aDur[i] || 0) * iters + (aDelay[i] || 0);
          if (end > now()) endTimes.push(end);
        }
      };

      nodes.forEach(n => {
        consider(n);
        n.querySelectorAll && n.querySelectorAll("*").forEach(consider);
      });

      const remaining = Math.max(0, Math.max(0, ...endTimes) - now()) + minIdle;
      return Math.min(2000, remaining); // cap a single sleep
    }
    """
    # Loop: sleep the suggested amount, then check again; break if suggestion ~0
    for _ in range(10):
        ms = page.evaluate(script, selector, min_idle_ms)
        if not ms or ms <= min_idle_ms // 2:
            # two RAFs to ensure paint flush
            page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(true))))")
            return
        page.wait_for_timeout(int(ms))


def settle_page(page: Page, *, selector: Optional[str] = None) -> None:
    """
    Composite: network idle → DOM stable → animations finished.
    Good default before screenshots or after navigation.
    """
    s = get_settings()
    if s.DETECT_ANIMATIONS or s.DETECT_OVERLAYS or s.DETECT_MODALS:
        try:
            wait_for_network_idle(page, timeout_ms=s.PAGE_LOAD_TIMEOUT)
        except Exception:
            pass
        try:
            wait_for_dom_stable(page, settle_ms=s.NETWORK_IDLE_TIMEOUT, max_wait_ms=s.PAGE_LOAD_TIMEOUT)
        except Exception:
            pass
        try:
            if s.DETECT_ANIMATIONS:
                wait_for_animations_to_finish(page, selector=selector, min_idle_ms=200)
        except Exception:
            pass

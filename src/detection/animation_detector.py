# src/detection/animation_detector.py
from __future__ import annotations

"""Animation/transition detector
-------------------------------
Inspects computed styles to estimate running transitions/animations and
provides a helper to wait until the UI is idle enough for stable screenshots.
"""

from dataclasses import dataclass
from typing import List, Optional

from playwright.sync_api import Page, Locator

from src.utils.logger import get_logger
from src.utils.config import get_settings
from src.utils.timing import measure


@dataclass
class RunningAnimation:
    selector_hint: str
    type: str           # "transition" | "animation"
    durations_ms: List[float]
    delays_ms: List[float]
    iteration_count: List[float]  # animations only; transitions will be [1]
    bounding: dict                 # x,y,width,height


class AnimationDetector:
    """
    Utilities to detect and wait for CSS transitions/animations to finish.
    Works across frameworks (React/Vue/…).
    """

    def __init__(self) -> None:
        self.log = get_logger(__name__)
        self.settings = get_settings()

    # ---------- Public API ----------

    def list_running(self, page: Page, root_selector: Optional[str] = None) -> List[RunningAnimation]:
        """
        Return a list of running (or scheduled) CSS transitions/animations under root_selector (or document).
        """
        script = """
        (rootSel) => {
          const nodes = rootSel ? Array.from(document.querySelectorAll(rootSel)) : [document.documentElement];
          const results = [];

          const push = (el, type, durs, dels, iters) => {
            const r = el.getBoundingClientRect();
            const hint = (el.tagName.toLowerCase()
                          + (el.id ? '#' + el.id : '')
                          + (el.className && typeof el.className === 'string'
                              ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.')
                              : ''));
            results.push({
              selector_hint: hint.slice(0, 120),
              type,
              durations_ms: durs,
              delays_ms: dels,
              iteration_count: iters,
              bounding: {x:r.left, y:r.top, width:r.width, height:r.height}
            });
          };

          const scan = (el) => {
            const cs = getComputedStyle(el);

            // transitions
            const tDur = cs.transitionDuration.split(',').map(s => parseFloat(s) * 1000 || 0);
            const tDelay = cs.transitionDelay.split(',').map(s => parseFloat(s) * 1000 || 0);
            if (tDur.some(v => v > 0)) {
              const n = Math.max(tDur.length, tDelay.length);
              const d = Array.from({length:n}, (_,i)=>tDur[i]||0);
              const l = Array.from({length:n}, (_,i)=>tDelay[i]||0);
              push(el, "transition", d, l, Array(n).fill(1));
            }

            // animations
            const aDur = cs.animationDuration.split(',').map(s => parseFloat(s) * 1000 || 0);
            const aDelay = cs.animationDelay.split(',').map(s => parseFloat(s) * 1000 || 0);
            const aIter = cs.animationIterationCount.split(',').map(v => v.trim()==='infinite' ? 1 : (parseFloat(v) || 0));
            if (aDur.some(v => v > 0)) {
              const n = Math.max(aDur.length, aDelay.length, aIter.length);
              const d = Array.from({length:n}, (_,i)=>aDur[i]||0);
              const l = Array.from({length:n}, (_,i)=>aDelay[i]||0);
              const it = Array.from({length:n}, (_,i)=>aIter[i]||1);
              push(el, "animation", d, l, it);
            }
          };

          const visit = (root) => {
            scan(root);
            root.querySelectorAll && root.querySelectorAll("*").forEach(scan);
          };

          nodes.forEach(visit);
          return results;
        }
        """
        data = page.evaluate(script, root_selector)
        return [RunningAnimation(**it) for it in data]

    @measure("wait_for_animations_to_finish")
    def wait_until_idle(self, page: Page, root_selector: Optional[str] = None, min_idle_ms: int = 200) -> None:
        """
        Wait until no transitions/animations are expected to run under the root.
        Uses computed durations + delays; caps single waits to 2s to avoid hangs.
        """
        script = """
        (rootSel, minIdle) => {
          const now = () => performance.now();
          const nodes = rootSel ? Array.from(document.querySelectorAll(rootSel)) : [document.documentElement];
          const endTimes = [];

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
              const iters = aIter[i] || 1;
              const end = now() + (aDur[i] || 0) * iters + (aDelay[i] || 0);
              if (end > now()) endTimes.push(end);
            }
          };

          const roots = nodes.flatMap(n => [n, ...(n.querySelectorAll ? Array.from(n.querySelectorAll("*")) : [])]);
          roots.forEach(consider);

          const remaining = Math.max(0, Math.max(0, ...endTimes) - now()) + minIdle;
          return Math.min(2000, remaining); // suggestion for a single sleep (ms)
        }
        """

        # loop a few times until suggested wait is almost zero
        for _ in range(10):
            ms = page.evaluate(script, root_selector, min_idle_ms)
            if not ms or ms <= min_idle_ms // 2:
                # ensure two paint cycles after idle
                page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(true))))")
                return
            page.wait_for_timeout(int(ms))

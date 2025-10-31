# src/detection/modal_detector.py
from __future__ import annotations

"""Modal detection heuristics
----------------------------
Locates the most prominent visible modal/dialog element using ARIA roles,
common class names, z-index, viewport intersection, and backdrop presence.
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple

from playwright.sync_api import Page, Locator

from src.utils.logger import get_logger


@dataclass
class ModalInfo:
    locator: Locator
    selector_hint: str
    z_index: int
    bbox: Tuple[int, int, int, int]  # x, y, width, height
    has_backdrop: bool


class ModalDetector:
    """
    Heuristic modal detector:
      - Candidates: [role="dialog"], [aria-modal="true"], <dialog>, class contains modal/dialog (case-insensitive)
      - Visible within viewport
      - Highest z-index wins
      - Optional backdrop detection (full-screen fixed/absolute semi-opaque layer)
    """
    CANDIDATE_CSS = (
        '[role="dialog"],[role="alertdialog"],[aria-modal="true"],dialog,'
        '[class*="modal" i],[class*="dialog" i],[data-testid*="modal" i],[data-overlay="true"]'
    )

    BACKDROP_CSS = (
        '.modal-backdrop,[class*="backdrop" i],[class*="overlay" i],[data-testid*="backdrop" i]'
    )

    def __init__(self):
        self.log = get_logger(__name__)

    # ---------------- Public API ----------------

    def detect_active(self, page: Page) -> Optional[ModalInfo]:
        """
        Return the most likely active modal on the page, or None.
        """
        candidates = page.locator(self.CANDIDATE_CSS)
        count = candidates.count()
        if count == 0:
            return None

        best: Optional[ModalInfo] = None

        for i in range(count):
            loc = candidates.nth(i)
            try:
                if not loc.is_visible():
                    continue
                z, bbox, in_vp = self._zindex_and_bbox(page, loc)
                if not in_vp:
                    continue
                has_backdrop = self._has_backdrop(page, loc)
                hint = self._hint_for(loc)
                info = ModalInfo(locator=loc, selector_hint=hint, z_index=z, bbox=bbox, has_backdrop=has_backdrop)
                if (best is None) or (info.z_index > best.z_index):
                    best = info
            except Exception:
                # Ignore flaky elements
                continue

        return best

    def find_all(self, page: Page) -> List[ModalInfo]:
        infos: List[ModalInfo] = []
        candidates = page.locator(self.CANDIDATE_CSS)
        for i in range(candidates.count()):
            loc = candidates.nth(i)
            try:
                if not loc.is_visible():
                    continue
                z, bbox, in_vp = self._zindex_and_bbox(page, loc)
                if not in_vp:
                    continue
                has_backdrop = self._has_backdrop(page, loc)
                infos.append(ModalInfo(locator=loc, selector_hint=self._hint_for(loc), z_index=z, bbox=bbox, has_backdrop=has_backdrop))
            except Exception:
                pass
        # sort desc by z-index
        infos.sort(key=lambda m: m.z_index, reverse=True)
        return infos

    # ---------------- Internals ----------------

    def _hint_for(self, loc: Locator) -> str:
        try:
            return loc.evaluate(
                """(el) => {
                    const id = el.id ? '#' + el.id : '';
                    const cls = el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.') : '';
                    return (el.tagName.toLowerCase() + id + cls).slice(0,120);
                }"""
            )
        except Exception:
            return "<element>"

    def _zindex_and_bbox(self, page: Page, loc: Locator) -> tuple[int, tuple[int,int,int,int], bool]:
        """
        Return computed z-index (int), bounding box, and whether element center is in viewport.
        """
        z = 0
        try:
            z = int(
                loc.evaluate(
                    """(el) => {
                        const cs = getComputedStyle(el);
                        const zi = parseInt(cs.zIndex, 10);
                        return Number.isFinite(zi) ? zi : 0;
                    }"""
                )
            )
        except Exception:
            z = 0

        bbox = loc.bounding_box() or {"x": 0, "y": 0, "width": 0, "height": 0}
        x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["width"]), int(bbox["height"])

        in_vp = False
        try:
            in_vp = bool(
                loc.evaluate(
                    """(el) => {
                        const r = el.getBoundingClientRect();
                        const cx = r.left + r.width/2, cy = r.top + r.height/2;
                        return cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight;
                    }"""
                )
            )
        except Exception:
            in_vp = (w > 0 and h > 0)
        return z, (x, y, w, h), in_vp

    def _has_backdrop(self, page: Page, modal: Locator) -> bool:
        """
        Heuristic backdrop detection: large fixed/absolute element behind modal with opacity.
        """
        # First: explicit backdrop matches
        backdrop = page.locator(self.BACKDROP_CSS)
        try:
            for i in range(min(5, backdrop.count())):
                if backdrop.nth(i).is_visible():
                    return True
        except Exception:
            pass

        # Fallback: scan siblings/ancestors for full-size overlay
        try:
            return bool(
                modal.evaluate(
                    """(el) => {
                        const isBackdrop = (n) => {
                          if (!n || !(n instanceof Element)) return false;
                          const cs = getComputedStyle(n);
                          if (!['fixed','absolute'].includes(cs.position)) return false;
                          const opaque = (parseFloat(cs.opacity) >= 0.1) || (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)');
                          const r = n.getBoundingClientRect();
                          const covers = r.width >= window.innerWidth*0.95 && r.height >= window.innerHeight*0.95;
                          return opaque && covers;
                        };
                        // check previous sibling (common pattern)
                        if (isBackdrop(el.previousElementSibling)) return true;
                        // check parent children (overlay before modal)
                        const p = el.parentElement;
                        if (p) {
                          for (const c of p.children) {
                            if (c === el) continue;
                            if (isBackdrop(c)) return true;
                          }
                        }
                        return false;
                    }"""
                )
            )
        except Exception:
            return False

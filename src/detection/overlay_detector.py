# src/detection/overlay_detector.py
from __future__ import annotations

"""Overlay/backdrop detection
----------------------------
Detects full-screen overlays/backdrops based on size, positioning, visibility,
and z-index to help characterize non-URL UI states (e.g., modal backdrops).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from playwright.sync_api import Page, Locator

from src.utils.logger import get_logger


@dataclass
class OverlayInfo:
    locator: Locator
    selector_hint: str
    z_index: int
    bbox: Tuple[int, int, int, int]  # x, y, width, height
    opacity: float


class OverlayDetector:
    """
    Detects full-screen overlays/backdrops (e.g., when a modal is open or a page is blocking input).
    Heuristics:
      - Large element (>= 90% of viewport size)
      - position: fixed/absolute
      - visible and opacity/background indicates coverage
      - High z-index relative to rest of page
    """

    CANDIDATE_CSS = (
        # common names
        '[class*="overlay" i],[class*="backdrop" i],[data-testid*="overlay" i],[data-testid*="backdrop" i],'
        # generic blockers
        '[aria-hidden="true"][style*="position: fixed"],[aria-hidden="true"][style*="position: absolute"]'
    )

    def __init__(self) -> None:
        self.log = get_logger(__name__)

    # -------------- Public API --------------

    def detect(self, page: Page) -> Optional[OverlayInfo]:
        """
        Return the most prominent overlay/backdrop if present; else None.
        """
        overlays = self.find_all(page)
        return overlays[0] if overlays else None

    def find_all(self, page: Page) -> List[OverlayInfo]:
        """
        Return overlays sorted by descending z-index.
        """
        res: List[OverlayInfo] = []
        locs = page.locator(self.CANDIDATE_CSS)
        count = locs.count()

        for i in range(count):
            loc = locs.nth(i)
            try:
                if not loc.is_visible():
                    continue
                info = self._score_overlay(page, loc)
                if info:
                    res.append(info)
            except Exception:
                continue

        res.sort(key=lambda x: (x.z_index, x.bbox[2] * x.bbox[3], x.opacity), reverse=True)
        return res

    # -------------- Internals --------------

    def _score_overlay(self, page: Page, loc: Locator) -> Optional[OverlayInfo]:
        """
        Return OverlayInfo if element looks like a true overlay/backdrop; otherwise None.
        """
        try:
            data = loc.evaluate(
                """(el) => {
                    const cs = getComputedStyle(el);
                    const pos = cs.position;
                    if (!['fixed','absolute'].includes(pos)) return null;

                    const r = el.getBoundingClientRect();
                    const vw = window.innerWidth, vh = window.innerHeight;

                    // must cover most of the viewport
                    const covers = r.width >= vw * 0.9 && r.height >= vh * 0.9;

                    // opacity/background check (allow semi-transparent)
                    const alpha = parseFloat(cs.opacity);
                    const bg = cs.backgroundColor || '';
                    const visibleBg = alpha >= 0.05 || (bg && bg !== 'rgba(0, 0, 0, 0)');

                    // compute z-index (NaN => 0)
                    const ziRaw = parseInt(cs.zIndex, 10);
                    const zi = Number.isFinite(ziRaw) ? ziRaw : 0;

                    if (!covers || !visibleBg) return null;

                    const hint = (el.tagName.toLowerCase()
                                  + (el.id ? '#' + el.id : '')
                                  + (el.className && typeof el.className === 'string'
                                      ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.')
                                      : '')).slice(0, 120);
                    return {
                      hint,
                      z: zi,
                      bbox: {x:r.left, y:r.top, w:r.width, h:r.height},
                      opacity: alpha || 0
                    };
                }"""
            )
            if not data:
                return None

            return OverlayInfo(
                locator=loc,
                selector_hint=data["hint"],
                z_index=int(data["z"]),
                bbox=(int(data["bbox"]["x"]), int(data["bbox"]["y"]), int(data["bbox"]["w"]), int(data["bbox"]["h"])),
                opacity=float(data["opacity"]),
            )
        except Exception:
            return None

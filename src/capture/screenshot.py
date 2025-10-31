# src/capture/screenshot.py
from __future__ import annotations

"""Screenshot utilities
----------------------
Provides a small manager to capture page/element screenshots with consistent
filenames and optional masking; returns structured capture results.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Tuple, List

from playwright.sync_api import Page, Locator

from src.utils.config import get_settings, Settings
from src.utils.logger import get_logger
from src.utils.timing import measure

try:
    # Optional masking/annotation support (blur boxes, etc.)
    from PIL import Image, ImageDraw, ImageFilter
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


@dataclass
class CaptureResult:
    path: Path
    width: int
    height: int
    kind: str            # "page" or "element"
    name: str            # logical name (e.g., step label)
    url: str
    title: str
    ts: str              # ISO timestamp


class ScreenshotManager:
    """
    Centralized screenshot helper.
    - Respects global settings (format/quality/full-page).
    - Produces deterministic file names.
    - Optional masking (requires Pillow).
    """

    def __init__(self, run_dir: Path):
        self.settings: Settings = get_settings()
        self.run_dir = run_dir
        self.log = get_logger(__name__)

    # ----------- Public API -----------

    @measure("page_screenshot")
    def page(
        self,
        page: Page,
        name: str,
        full_page: Optional[bool] = None,
        masks: Optional[List[Locator]] = None,
    ) -> CaptureResult:
        """
        Capture the page.
        Args:
            name: base filename (no extension)
            full_page: override config FULL_PAGE_SCREENSHOT
            masks: optional list of locators to blur/box (requires Pillow)
        """
        fmt = self.settings.SCREENSHOT_FORMAT.value
        is_full = full_page if full_page is not None else self.settings.FULL_PAGE_SCREENSHOT

        out_path = self._build_path(name, fmt)
        page.screenshot(
            path=str(out_path),
            type=fmt,
            full_page=is_full,
            quality=(self.settings.SCREENSHOT_QUALITY if fmt == "jpeg" else None),
        )

        if masks:
            self._apply_masks(out_path, page, masks)

        w, h = self._image_size(out_path)
        return CaptureResult(
            path=out_path,
            width=w,
            height=h,
            kind="page",
            name=name,
            url=page.url,
            title=(page.title() or ""),
            ts=self._ts(),
        )

    @measure("element_screenshot")
    def element(
        self,
        page: Page,
        locator: Locator,
        name: str,
        masks: Optional[List[Locator]] = None,
    ) -> CaptureResult:
        """
        Capture a specific element.
        """
        fmt = self.settings.SCREENSHOT_FORMAT.value
        out_path = self._build_path(name, fmt)
        locator.screenshot(
            path=str(out_path),
            type=fmt,
            quality=(self.settings.SCREENSHOT_QUALITY if fmt == "jpeg" else None),
        )

        # Masks on element screenshots are uncommon; if provided, we try.
        if masks:
            self._apply_masks(out_path, page, masks)

        w, h = self._image_size(out_path)
        return CaptureResult(
            path=out_path,
            width=w,
            height=h,
            kind="element",
            name=name,
            url=page.url,
            title=(page.title() or ""),
            ts=self._ts(),
        )

    # ----------- Internals -----------

    def _build_path(self, base: str, ext: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in base)
        out_path = self.run_dir / f"{safe}.{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return out_path

    def _image_size(self, path: Path) -> Tuple[int, int]:
        if PIL_AVAILABLE:
            try:
                with Image.open(path) as im:
                    return im.width, im.height
            except Exception:
                pass
        # Fallback: unknown size
        return (0, 0)

    def _apply_masks(self, img_path: Path, page: Page, locators: List[Locator]) -> None:
        """
        Blur rectangular regions for the provided locators.
        No-op if Pillow is not available or if any bbox can't be computed.
        """
        if not PIL_AVAILABLE:
            self.log.debug("Pillow not available; skipping masks.")
            return

        try:
            with Image.open(img_path) as im:
                overlay = im.copy()
                for loc in locators:
                    bbox = loc.bounding_box()
                    if not bbox:
                        continue
                    left = int(bbox["x"])
                    top = int(bbox["y"])
                    right = int(bbox["x"] + bbox["width"])
                    bottom = int(bbox["y"] + bbox["height"])

                    region = overlay.crop((left, top, right, bottom))
                    region = region.filter(ImageFilter.GaussianBlur(radius=12))
                    overlay.paste(region, (left, top))

                overlay.save(img_path)
        except Exception as e:
            self.log.debug(f"Failed to apply masks: {e!r}")

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

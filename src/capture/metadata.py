# src/capture/metadata.py
from __future__ import annotations

"""Per-image metadata helpers
----------------------------
Builds and writes sidecar JSON for each capture and appends a JSONL stream
for quick indexing/analysis across a run.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Page

from src.utils.config import get_settings
from src.utils.logger import get_logger
from src.capture.screenshot import CaptureResult


@dataclass
class CaptureMeta:
    """
    Sidecar metadata for a single capture.
    Stored next to the image as <image>.json and also appended to captures.jsonl.
    """
    name: str
    path: str
    kind: str              # "page" | "element"
    width: int
    height: int
    page_url: str
    page_title: str
    ts: str
    step_index: Optional[int] = None
    step_action: Optional[str] = None
    site: Optional[str] = None
    task: Optional[str] = None
    extra: Dict[str, Any] = None


class MetadataBuilder:
    """
    Writes per-capture sidecars and appends to a run-scoped JSONL file.
    Engine still writes a run-level manifest.json; this is focused on per-image detail.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.jsonl_path = run_dir / "captures.jsonl"
        self.settings = get_settings()
        self.log = get_logger(__name__)

    def from_capture(
        self,
        cap: CaptureResult,
        *,
        step_index: Optional[int] = None,
        step_action: Optional[str] = None,
        site: Optional[str] = None,
        task: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> CaptureMeta:
        return CaptureMeta(
            name=cap.name,
            path=str(cap.path.relative_to(self.run_dir) if cap.path.is_relative_to(self.run_dir) else cap.path),
            kind=cap.kind,
            width=cap.width,
            height=cap.height,
            page_url=cap.url,
            page_title=cap.title,
            ts=cap.ts,
            step_index=step_index,
            step_action=step_action,
            site=site,
            task=task,
            extra=extra or {},
        )

    def write_sidecar(self, meta: CaptureMeta) -> Path:
        """
        Write <image>.json next to the image.
        """
        img_path = self.run_dir / meta.path if not Path(meta.path).is_absolute() else Path(meta.path)
        sidecar = img_path.with_suffix(img_path.suffix + ".json")
        sidecar.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
        return sidecar

    def append_jsonl(self, meta: CaptureMeta) -> None:
        """
        Append to captures.jsonl (one JSON object per line).
        """
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(meta), ensure_ascii=False) + "\n")

    def record(
        self,
        cap: CaptureResult,
        *,
        step_index: Optional[int],
        step_action: Optional[str],
        site: Optional[str],
        task: Optional[str],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Convenience: build meta, write sidecar, append jsonl. Returns dict for immediate use.
        """
        meta = self.from_capture(
            cap,
            step_index=step_index,
            step_action=step_action,
            site=site,
            task=task,
            extra=extra,
        )
        self.write_sidecar(meta)
        self.append_jsonl(meta)
        return asdict(meta)

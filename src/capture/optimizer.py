# src/capture/optimizer.py
from __future__ import annotations

import io
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

from src.utils.config import get_settings
from src.utils.logger import get_logger

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


SUPPORTED_EXTS = {".png", ".jpg", ".jpeg"}
JPEG_EXTS = {".jpg", ".jpeg"}


@dataclass
class OptimizeStats:
    path: Path
    before_kb: int
    after_kb: int
    saved_kb: int
    changed: bool


class ImageOptimizer:
    """
    Safe, idempotent optimizer for PNG/JPEG using Pillow.

    Features:
      - Strips metadata/EXIF
      - Recompress PNG with optimize=True; optional palette quantization
      - Recompress JPEG with quality/progressive
      - Optional downscale if file over MAX_IMAGE_SIZE_KB
      - Temp-file write; replace only if smaller (prevents corruption/regressions)
    """

    def __init__(
        self,
        max_size_kb: int | None = None,
        png_quantize: bool = True,
        jpeg_quality: int | None = None,
        progressive_jpeg: bool = True,
    ):
        s = get_settings()
        self.log = get_logger(__name__)

        # Defaults from settings if not passed
        self.max_size_kb = max_size_kb if max_size_kb is not None else int(s.MAX_IMAGE_SIZE_KB or 0)
        self.png_quantize = png_quantize
        self.jpeg_quality = (
            jpeg_quality if jpeg_quality is not None else (s.SCREENSHOT_QUALITY if "jpeg" else 90)
        )
        self.progressive_jpeg = progressive_jpeg

        if not PIL_AVAILABLE:
            self.log.warning("Pillow not available—optimizer will be a no-op.")

    # ---------- Public API ----------

    def optimize_run_dir(self, run_dir: Path) -> list[OptimizeStats]:
        """
        Optimize all PNG/JPEG images in a run directory (non-recursive).
        """
        results: list[OptimizeStats] = []
        if not PIL_AVAILABLE:
            return results

        for p in sorted(run_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                try:
                    results.append(self.optimize_file(p))
                except Exception as e:
                    self.log.debug(f"Skip optimize {p.name}: {e!r}")
        return results

    def optimize_file(self, path: Path) -> OptimizeStats:
        """
        Optimize a single image. Returns stats even if unchanged.
        """
        before = _file_kb(path)
        if not PIL_AVAILABLE:
            return OptimizeStats(path, before, before, 0, False)

        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            return OptimizeStats(path, before, before, 0, False)

        with Image.open(path) as im:
            im = self._strip_metadata(im)

            if ext in JPEG_EXTS:
                buf = self._encode_jpeg(im)
            else:
                buf = self._encode_png(im)

            # If size cap configured and still too big, attempt downscale loop
            if self.max_size_kb and (len(buf) // 1024) > self.max_size_kb:
                buf = self._downscale_until(im, ext, target_kb=self.max_size_kb, initial_bytes=buf)

        after = self._maybe_replace(path, buf)
        return OptimizeStats(path, before, after, max(0, before - after), after < before)

    # ---------- Internals ----------

    def _encode_png(self, im: Image.Image) -> bytes:
        """
        Re-encode PNG with optimize=True and optional 8-bit palette quantization.
        """
        img = im
        if self.png_quantize:
            try:
                # Convert to P mode (palette) only if it meaningfully reduces (skip photos)
                if img.mode in ("RGBA", "LA", "RGB", "L"):
                    q = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
                    if _estimate_png_bytes(q) < _estimate_png_bytes(img):
                        img = q
            except Exception:
                pass

        out = io.BytesIO()
        try:
            img.save(out, format="PNG", optimize=True)
        except OSError:
            # Some PNGs still fail optimize=True; fallback without it
            out = io.BytesIO()
            img.save(out, format="PNG")
        return out.getvalue()

    def _encode_jpeg(self, im: Image.Image) -> bytes:
        """
        Re-encode JPEG: strip EXIF, convert to RGB if needed, set quality & progressive.
        """
        img = im
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        quality = int(self.jpeg_quality or 85)
        out = io.BytesIO()
        img.save(
            out,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=self.progressive_jpeg,
            subsampling="4:2:0",
        )
        return out.getvalue()

    def _downscale_until(self, im: Image.Image, ext: str, target_kb: int, initial_bytes: bytes) -> bytes:
        """
        Downscale by 10% steps until under target_kb or until no further improvement.
        Keeps aspect ratio. Uses antialiasing.
        """
        current = initial_bytes
        current_kb = len(current) // 1024
        if current_kb <= target_kb:
            return current

        scale = 0.9
        img = im
        last_len = len(current)

        for _ in range(10):  # cap attempts
            w, h = img.size
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            if nw == w or nh == h:
                break
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)

            if ext.lower() in JPEG_EXTS:
                current = self._encode_jpeg(img)
            else:
                current = self._encode_png(img)

            if (len(current) // 1024) <= target_kb:
                break
            # stop if not improving
            if len(current) >= last_len:
                break
            last_len = len(current)

        return current

    def _maybe_replace(self, path: Path, new_bytes: bytes) -> int:
        """
        Replace the original with `new_bytes` only if it's smaller.
        Write via temp file for safety. Return final size (KB).
        """
        before_kb = _file_kb(path)
        after_kb = len(new_bytes) // 1024

        if after_kb >= before_kb:
            return before_kb

        tmp_dir = Path(tempfile.mkdtemp(prefix="optimg_"))
        tmp_path = tmp_dir / path.name
        try:
            with open(tmp_path, "wb") as f:
                f.write(new_bytes)
            # Atomic-ish move
            shutil.move(str(tmp_path), str(path))
            return after_kb
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    @staticmethod
    def _strip_metadata(im: Image.Image) -> Image.Image:
        """
        Remove EXIF/ICC to reduce size and avoid leaking info.
        """
        try:
            data = list(im.getdata())
            clean = Image.new(im.mode, im.size)
            clean.putdata(data)
            return clean
        except Exception:
            return im


# ---------- helpers ----------

def _file_kb(p: Path) -> int:
    try:
        return max(0, int(os.path.getsize(p) // 1024))
    except Exception:
        return 0


def _estimate_png_bytes(im: Image.Image) -> int:
    """
    Quick in-memory estimate of PNG size for decision-making.
    """
    try:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return len(buf.getvalue())
    except Exception:
        return 1 << 30  # huge sentinel => "don't choose this"

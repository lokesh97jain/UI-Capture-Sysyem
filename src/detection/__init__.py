# src/detection/__init__.py
"""
Detection package
-----------------
Lightweight init to avoid import-time errors and circular imports.
Exports only the classes/functions that exist, keeping side effects minimal.
Consumers can also import submodules directly (e.g., src.detection.modal_detector).
"""

from .modal_detector import ModalDetector, ModalInfo
from .animation_detector import AnimationDetector
from .overlay_detector import OverlayDetector, OverlayInfo
from .stability import settle_page

__all__ = [
    "ModalDetector",
    "ModalInfo",
    "AnimationDetector",
    "OverlayDetector",
    "OverlayInfo",
    "settle_page",
]

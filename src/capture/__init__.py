"""
Capture package for UI Capture System.
Handles screenshots, metadata generation, and related utilities.
"""

from .screenshot import ScreenshotManager
from .metadata import MetadataBuilder

__all__ = [
    "ScreenshotManager",
    "MetadataBuilder",
]

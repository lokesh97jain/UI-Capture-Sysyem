# # src/core/__init__.py
# """
# Core package for UI Capture System.
# Provides workflow parsing, action handling, and orchestration engine.
# """

# from .workflow_loader import Workflow, load_workflow
# from .actions import Actions
# from .engine import CaptureEngine

# __all__ = [
#     "Workflow",
#     "load_workflow",
#     "Actions",
#     "CaptureEngine",
# ]
"""
Core package for UI Capture System.
Lightweight package init to avoid import cycles.

Consumers should import submodules directly, e.g.:
  from src.core.workflow_loader import load_workflow, Workflow
  from src.core.actions import execute_step
  from src.core.engine import run_workflow, Engine
"""

__all__: list[str] = []

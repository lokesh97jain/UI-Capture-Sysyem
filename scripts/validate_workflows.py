# scripts/validate_workflows.py
"""
Validate all YAML workflows under ./workflows directory.
Run: python scripts/validate_workflows.py
"""

from pathlib import Path
from src.core.workflow_loader import WorkflowLoader
from src.utils.logger import get_logger

def main():
    log = get_logger(__name__)
    loader = WorkflowLoader()
    root = Path("workflows")

    if not root.exists():
        log.error("No workflows/ directory found.")
        return

    workflows = loader.load_directory(root)
    log.info(f"Validated {len(workflows)} workflow(s).")

if __name__ == "__main__":
    main()

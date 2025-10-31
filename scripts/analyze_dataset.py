# scripts/analyze_dataset.py
"""
Analyze dataset directory for captured screenshots and metadata.
Run: python scripts/analyze_dataset.py
"""

import json
from pathlib import Path
from collections import Counter
from src.utils.logger import get_logger

def main():
    log = get_logger(__name__)
    root = Path("datasets")
    if not root.exists():
        log.error("No datasets directory found.")
        return

    screenshots = list(root.rglob("*.png")) + list(root.rglob("*.jpeg"))
    metas = list(root.rglob("*.json"))

    log.info(f"Found {len(screenshots)} images and {len(metas)} metadata files.")
    ext_count = Counter(p.suffix for p in screenshots)
    log.info(f"Image formats: {dict(ext_count)}")

    # quick metadata stats
    if metas:
        sizes = []
        for f in metas:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sizes.append(len(json.dumps(data)))
            except Exception:
                pass
        avg = sum(sizes) / len(sizes) if sizes else 0
        log.info(f"Avg metadata size: {avg:.1f} bytes")

if __name__ == "__main__":
    main()

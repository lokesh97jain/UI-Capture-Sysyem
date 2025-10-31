"""
Generate a simple datasets index JSON summarizing captured runs.

Usage:
  python scripts/generate_index.py [output_path]

Writes a JSON with structure:
{
  "generated_at": "...",
  "runs": [
     {"app": "linear.app", "task": "create_issue", "run_dir": "...", "images": ["..."], "meta": ["..."]}
  ]
}
"""

import json
import sys
from pathlib import Path
from datetime import datetime


def main():
    datasets_root = Path("datasets")
    if not datasets_root.exists():
        print("No datasets directory found.")
        return 1

    runs = []
    for app_dir in sorted(datasets_root.iterdir()):
        if not app_dir.is_dir():
            continue
        for task_dir in sorted(app_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            for run_dir in sorted(task_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                images = sorted([str(p) for p in run_dir.glob("*.png")])
                meta = sorted([str(p) for p in run_dir.glob("*.json")])
                runs.append(
                    {
                        "app": app_dir.name,
                        "task": task_dir.name,
                        "run_dir": str(run_dir),
                        "images": images,
                        "meta": meta,
                    }
                )

    out = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "runs": runs,
    }

    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else datasets_root / "index.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote dataset index: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


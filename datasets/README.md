Datasets Overview
=================

This folder contains captured runs organized by app/site and task.
Each run directory includes screenshots, per-image metadata, a JSONL index, and run summaries.

Layout
------
- `datasets/<site>/<task>/<YYYY-MM-DD_HH-MM-SS>/`
  - `NN_slug.png` — screenshots captured during the workflow
  - `NN_slug.png.json` — sidecar metadata for each image
  - `captures.jsonl` — one JSON record per image (same fields as sidecars)
  - `manifest.json` — minimal run manifest (site/task/created_at/steps)
  - `metadata.json` — run-level summary with per-step details
  - `run.log` — per-run logs (useful for debugging selectors/timeouts)

Browse Runs
-----------
- Use your file explorer to open `datasets/<site>/<task>/` and sort the run folders by timestamp.
- Open `metadata.json` to skim the run quickly (step descriptions, URLs, UI state flags).
- Open `captures.jsonl` in a JSON viewer or feed it to tools for indexing/analysis.

Captured Tasks (Examples)
-------------------------
- GitHub
  - `create_repository` — navigates to new repo form, fills fields, toggles privacy, and captures success state.
- Notion
  - `create_database` — opens New menu, selects Database, and captures the new DB state.
- Linear
  - `create_project` / `filter_issues` — example workflows to capture common actions.

Regenerate Index
----------------
- Create or refresh a global index of all runs:
  - `python scripts/generate_index.py` → writes `datasets/index.json`

Tips
----
- Prefer role/text selectors in workflows to improve stability across UI updates.
- Add short waits (`wait_for_network_idle`, `wait`, `after_wait_ms`) before screenshots.
- Use `assert_visible` on key UI elements to fail early and produce actionable logs.


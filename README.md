UI Capture System
=================

Overview
--------
- Captures UI states as screenshots for defined workflows so upstream agents can reason over real interfaces in real time.
- Generalizes across web apps by describing tasks in YAML (navigation + interactions + screenshots).
- Produces a clean dataset per run, including images, sidecar metadata, a JSONL index, and a run-level `metadata.json` summary.

Key Features
------------
- Playwright-driven execution with storage-state auth.
- YAML workflows with robust selectors (`role`, `text`, `css`, `xpath`).
- Automatic per-image sidecars (`<image>.<ext>.json`) and `captures.jsonl` via `MetadataBuilder`.
- Run-level `metadata.json` summarizing steps, URLs, and UI state (modal/overlay hints).
- Heuristics to detect modals/overlays/animations to stabilize screenshots.

Repository Structure
--------------------
- `src/cli.py` — CLI to list/validate/run workflows.
- `src/core/engine.py` — Orchestrates browser, runs workflows, emits artifacts.
- `src/core/actions.py` — Executes individual steps, handles screenshots + metadata.
- `src/core/workflow_loader.py` — Workflow schema/models and YAML loading.
- `src/capture/screenshot.py` — Page/element screenshot helper with optional masking.
- `src/capture/metadata.py` — Sidecar builder + `captures.jsonl` writer.
- `src/detection/*.py` — Modal/overlay/animation detection and stability helpers.
- `workflows/` — App-specific tasks, e.g. `github.com/create_repository.yaml`.
- `datasets/` — Output dataset tree. One folder per app/task/run.
- `storage_state/` — Saved login storage state per site.

Project Structure (High‑Level)
------------------------------
```
ui-capture-system/
├─ src/
│  ├─ cli.py
│  ├─ core/
│  │  ├─ engine.py
│  │  ├─ actions.py
│  │  └─ workflow_loader.py
│  ├─ capture/
│  │  ├─ screenshot.py
│  │  └─ metadata.py
│  ├─ detection/
│  │  ├─ modal_detector.py
│  │  ├─ overlay_detector.py
│  │  ├─ animation_detector.py
│  │  └─ stability.py
│  └─ utils/
│     ├─ config.py
│     ├─ logger.py
│     └─ timing.py
├─ workflows/
│  ├─ github.com/
│  │  └─ create_repository.yaml
│  ├─ linear.app/
│  │  ├─ create_project.yaml
│  │  └─ filter_issues.yaml
│  └─ www.notion.so/
│     ├─ create_database.yaml
│     └─ filter_database.yaml
├─ datasets/                # generated outputs
├─ storage_state/           # persisted auth per site
└─ scripts/
   ├─ setup_auth.py         # interactively save storage state
   ├─ generate_index.py     # build datasets/index.json
   └─ analyze_dataset.py    # quick dataset stats
```

Installation
------------
- Requirements: Python 3.10+, Playwright browsers.
- Install Python deps:
  - `pip install -r requirements.txt`
- Install Playwright browsers:
  - `python -m playwright install --with-deps`

Configuration
-------------
- Copy `.env.example` to `.env` and adjust settings. Highlights:
  - `HEADLESS=true|false` — Run browsers headless.
  - `BROWSER_TYPE=chromium|firefox|webkit` — Browser engine.
  - `OUTPUT_DIR=datasets` — Root for captures.
  - `STORAGE_STATE_DIR=storage_state` — Persisted auth state.
  - Timeouts and retry knobs: `PAGE_LOAD_TIMEOUT`, `MAX_RETRIES`, `RETRY_DELAY`.

Authentication
--------------
- The engine uses Playwright storage state per site (`storage_state/<site>.json`).
- If `use_storage_state: true` and no state file is found, the engine can open an interactive window to sign in and then saves the storage state.
- You can also pre-create state files using `scripts/setup_auth.py` (open, sign in, save).

CLI Usage
---------
- Show effective config: `python -m src.cli config`
- List workflows: `python -m src.cli list --dir workflows`
- Validate YAML: `python -m src.cli validate --dir workflows`
- Run a workflow: `python -m src.cli run workflows/github.com/create_repository.yaml`
- Run all in a folder: `python -m src.cli run --dir workflows/github.com`

Execution Workflow (Runtime)
----------------------------
- Load workflow YAML and validate schema.
- Launch Playwright browser/context (optionally with storage state).
- Sequentially execute steps (goto/click/type/assert/screenshot...).
- Before/after screenshots, apply short stability waits and detect modal/overlay state.
- Save images, per-image sidecars, `captures.jsonl`, `manifest.json`, `metadata.json`, and `run.log` to the run directory.

Project Workflow (Development)
------------------------------
- Author or tweak workflow YAMLs under `workflows/<site>/` using role/text selectors.
- Validate: `python -m src.cli validate --dir workflows`
- Test a single workflow: `python -m src.cli run <path/to/workflow.yaml>`
- Review outputs in `datasets/<site>/<task>/<timestamp>/` and iterate selectors/waits.
- Regenerate dataset index: `python scripts/generate_index.py`

Workflow YAML (Schema Essentials)
---------------------------------
- Top-level keys:
  - `version: "1"`
  - `site: "github.com"` (app/site key used for output grouping + storage state)
  - `task: "create_repository"` (task id used in paths)
  - `description: "..."` (optional)
  - `use_storage_state: true` (recommended)
  - `steps: [...]` (sequence of actions)
- Actions supported (selected): `goto`, `wait_for_network_idle`, `wait`, `wait_for_selector`, `click`, `type`, `fill`, `press`, `check`, `uncheck`, `select_option`, `set_input_files`, `assert_visible`, `assert_text`, `assert_url_contains`, `screenshot`.
- Selectors: `strategy: role|text|css|xpath`, `value: "..."`, `timeout_ms: 15000`.
- Example snippet:
  - `- action: screenshot
     name: 03_modal_open`

Outputs and Dataset Format
--------------------------
A successful run produces a folder like:
- `datasets/<site>/<task>/<YYYY-MM-DD_HH-MM-SS>/`
  - `00_home.png`, `01_modal.png`, ... — screenshots
  - `<image>.png.json` — sidecar metadata for each screenshot
  - `captures.jsonl` — one JSON object per capture (sidecar stream)
  - `manifest.json` — run manifest (site/task/created_at/steps)
  - `metadata.json` — run-level summary (see below)
  - `run.log` — per-run logs

Run-Level metadata.json
-----------------------
- Generated at the end of each run by the engine. Example shape (simplified):
- Keys:
  - `task`, `app`, `description`, `url`, `captured_at`, `total_steps`, `execution_time_seconds`
  - `steps`: list of per-step entries containing:
    - `step_number`, `screenshot`, `description`, `timestamp`
    - `url`: `{ current, has_unique_url, changed_from_previous }`
    - `action`: `{ type, selector }`
    - `ui_state`: `{ has_modal, has_overlay, page_title }`
    - `capture_reason`: heuristic label (e.g., `workflow_start`, `modal_appeared`, `url_changed`, `step_executed`)
  - `statistics`: counts and derived timings.

Per-Image Sidecar Metadata
--------------------------
- For each image `X.png`, a `X.png.json` is written with fields:
  - `name`, `path`, `kind` (page|element), `width`, `height`, `page_url`, `page_title`, `ts`
  - Optional: `step_index`, `step_action`, `site`, `task`, `extra`
- A copy of the same record is appended to `captures.jsonl` (one JSON per line).

Examples
--------
- GitHub: `workflows/github.com/create_repository.yaml` captures repo creation form states, dropdowns, and success page.
- Notion: `workflows/www.notion.so/create_database.yaml` captures new DB creation flow, including menus and panel states.
- Linear: sample workflows for creating project/issue and filtering issues.

Tips for Stable Captures
------------------------
- Prefer `role` and `text` selectors for resilience over brittle CSS.
- Add `wait_for_network_idle` and small `wait` steps before screenshots.
- Use `assert_visible` to fail early when key UI is missing.
- Consider `after_wait_ms` (per step or workflow default) to absorb animations.

Troubleshooting
---------------
- Auth prompts: ensure `use_storage_state: true` and sign in once to save storage.
- Timeouts: increase `PAGE_LOAD_TIMEOUT` or per-selector `timeout_ms`.
- Selectors: test with Playwright inspector (`PWDEBUG=1`) and prefer semantic selectors.
- Circular imports: package `__init__` files are intentionally lightweight; import from submodules where needed.


Development
-----------
- Validate all workflows: `python -m src.cli validate --dir workflows`
- Generate dataset index: `python scripts/generate_index.py`
- Analyze dataset quick stats: `python scripts/analyze_dataset.py`

Contributing
------------
- Keep workflow YAMLs small and focused on meaningful UI states.
- Use concise docstrings and add comments only where logic is non-obvious.
- Follow existing naming conventions for screenshots: `NN_slug.png`.

How To Run Everything (End‑to‑End)
----------------------------------
1) Install deps and browsers
   - Fast path: `python bootstrap_install.py` (installs Python deps and Playwright browsers)
   - Or manual:
     - `pip install -r requirements.txt`
     - `python -m playwright install --with-deps`
2) Configure `.env`
   - Set `OUTPUT_DIR`, `STORAGE_STATE_DIR`, timeouts, and headless mode.
   - Quick edit on Windows: `notepad .env`
   - `HEADLESS=true` runs captures in background; `HEADLESS=false` opens a visible browser.
3) Save auth (one time per site)
   - `python scripts/setup_auth.py` to launch a browser and sign in; the session is saved to `storage_state/<site>.json`.
   - You can also run a workflow with `use_storage_state: true` and follow the interactive login prompt on first navigation.
4) Validate workflows
   - `python -m src.cli validate --dir workflows`
5) Run a workflow
   - GitHub – create repository:
     - `python -m src.cli run workflows/github.com/create_repository.yaml`
   - Notion – create database:
     - `python -m src.cli run workflows/www.notion.so/create_database.yaml`
   - Linear – create issue:
     - `python -m src.cli run workflows/linear.app/create_issue.yaml`
   - Linear – create project:
     - `python -m src.cli run workflows/linear.app/create_project.yaml`
6) Inspect outputs
   - Browse `datasets/<site>/<task>/<timestamp>/` for images, sidecars, logs, and `metadata.json`.
7) Build dataset index
   - `python scripts/generate_index.py` (writes `datasets/index.json`).

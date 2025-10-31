# src/cli.py
from __future__ import annotations

"""Command-line interface
------------------------
Convenience commands to list/validate/run workflows and view effective config.
Thin wrapper around the core loader and engine for local runs.
"""
import os, re, tempfile

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import click

from src.utils.config import get_settings
from src.utils.logger import get_logger, bind, unbind, set_log_level
from src.core.workflow_loader import load_workflow, load_workflows_file, Workflow
from src.core.engine import run_workflow


# -------- helpers --------


def _echo_json(obj) -> None:
    click.echo(json.dumps(obj, indent=2, ensure_ascii=False))


def _resolve_paths(paths: List[str]) -> List[Path]:
    return [Path(p).resolve() for p in paths]


def _find_yaml_files(root: Path, recursive: bool = True) -> list[Path]:
    if recursive:
        return sorted(list(root.rglob("*.yaml")) + list(root.rglob("*.yml")))
    return sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml")))


# -------- CLI root --------


@click.group(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    default=None,
    help="Override LOG_LEVEL from settings",
)
@click.option("--color/--no-color", default=None, help="Force-enable/disable colorized console output")
@click.version_option(package_name="ui-capture-system")
def cli(log_level: Optional[str], color: Optional[bool]):
    # Initialize settings + logger once at process start
    _ = get_settings()
    if log_level:
        set_log_level(log_level.upper())


# -------- commands --------


@cli.command("config")
def cmd_config():
    """Print effective configuration (after .env & env vars)."""
    s = get_settings()
    data = {k: (str(v) if isinstance(v, Path) else v) for k, v in s.__dict__.items()}
    _echo_json(data)


@cli.command("list")
@click.option(
    "--dir", "workflows_dir",
    type=click.Path(file_okay=False, dir_okay=True, exists=True),
    default=lambda: str(get_settings().WORKFLOWS_DIR),
    show_default=True,
    help="Directory containing workflow YAML files",
)
@click.option("--recursive/--no-recursive", default=True, show_default=True)
@click.option("--app", "filter_app", type=str, default=None, help="Filter by app/site key (e.g., linear.app)")
def cmd_list(workflows_dir: str, recursive: bool, filter_app: Optional[str]):
    """List workflows available in a directory."""
    root = Path(workflows_dir)
    files = _find_yaml_files(root, recursive=recursive)

    rows = []
    for fp in files:
        try:
            wfs = load_workflows_file(fp)
            for wf in wfs:
                if filter_app and getattr(wf, "site", getattr(wf, "app", "")) != filter_app:
                    continue
                rows.append((fp, wf))
        except Exception:
            # skip invalid files silently here; use `validate` for details
            continue

    if not rows:
        click.echo("No workflows found.")
        return

    click.echo(f"Found {len(rows)} workflow(s):\n")
    for fp, wf in rows:
        site = getattr(wf, "site", getattr(wf, "app", "unknown"))
        name = getattr(wf, "task", getattr(wf, "name", "<unnamed>"))
        click.echo(f" - [{site}] {name}  ({len(wf.steps)} steps)  <- {fp}")


@cli.command("validate")
@click.argument("targets", nargs=-1, required=False)
@click.option("--dir", "workflows_dir", type=click.Path(file_okay=False, dir_okay=True, exists=True), help="Validate all workflows under this directory")
@click.option("--recursive/--no-recursive", default=True, show_default=True)
def cmd_validate(targets: List[str], workflows_dir: Optional[str], recursive: bool):
    """Validate workflows from files or a directory (supports multi-doc YAML)."""
    paths: list[Path] = []
    if targets:
        for p in _resolve_paths(targets):
            if p.is_dir():
                paths.extend(_find_yaml_files(p, recursive=True))
            else:
                paths.append(p)
    elif workflows_dir:
        paths.extend(_find_yaml_files(Path(workflows_dir), recursive=recursive))
    else:
        click.echo("Provide file(s) or --dir to validate.")
        sys.exit(2)

    ok = True
    for fp in paths:
        try:
            for wf in load_workflows_file(fp):
                site = getattr(wf, "site", getattr(wf, "app", "unknown"))
                name = getattr(wf, "task", getattr(wf, "name", "<unnamed>"))
                click.echo(f"OK  {fp}  ->  [{site}] {name} ({len(wf.steps)} steps)")
        except Exception as e:
            ok = False
            click.echo(f"ERR {fp}  ->  {e}")

    sys.exit(0 if ok else 1)



@cli.command("run")
@click.argument("targets", nargs=-1, required=False)
@click.option("--dir", "workflows_dir", type=click.Path(file_okay=False, dir_okay=True, exists=True),
              help="Run all workflows found under this directory (filtered by --app/--tags)")
@click.option("--recursive/--no-recursive", default=True, show_default=True)
@click.option("--app", "filter_app", type=str, default=None, help="Filter by app/site key when using --dir")
@click.option("--parallel/--no-parallel", default=None, help="Override PARALLEL_EXECUTION from settings")
@click.option("--max-workers", type=int, default=None, help="Override MAX_WORKERS from settings")
@click.option("--optimize/--no-optimize", default=None, help="Override OPTIMIZE_IMAGES from settings")
@click.option("--json-out", type=click.Path(dir_okay=False), default=None, help="Write a JSON summary to this file")
@click.option("--interactive-auth/--no-interactive-auth", default=True, show_default=True, help="Enable interactive login fallback if auth is needed")
## interactive-auth flag is handled internally by Engine with default True in run_workflow shim

def cmd_run(
    targets: List[str],
    workflows_dir: Optional[str],
    recursive: bool,
    filter_app: Optional[str],
    parallel: Optional[bool],
    max_workers: Optional[int],
    optimize: Optional[bool],
    json_out: Optional[str],
    interactive_auth: bool,
):
    """
    Run one or more workflows.

    Examples:
      ui-capture run workflows/www.notion.so/create_database.yaml
      ui-capture run --dir workflows --app www.notion.so --parallel
    """
    settings = get_settings()
    log = get_logger(__name__)

    # Using top-level run_workflow shim; no explicit Engine instance needed

    # Collect workflows
    workflows: List[Path] = []
    if targets:
        for p in _resolve_paths(targets):
            if p.is_dir():
                workflows.extend(_find_yaml_files(p, recursive=True))
            else:
                workflows.append(p)
    elif workflows_dir:
        files = _find_yaml_files(Path(workflows_dir), recursive=recursive)
        # Filter by site/app if requested
        for fp in files:
            try:
                wf = load_workflow(fp)
                site = getattr(wf, "site", getattr(wf, "app", ""))
                if filter_app and site != filter_app:
                    continue
                workflows.append(fp)
            except Exception:
                # skip invalids
                pass
    else:
        click.echo("Nothing to run. Provide file(s) or --dir.")
        sys.exit(2)

    if not workflows:
        click.echo("No workflows matched.")
        sys.exit(1)

    # Execution mode
    run_parallel = settings.PARALLEL_EXECUTION if parallel is None else bool(parallel)
    workers = settings.MAX_WORKERS if max_workers is None else int(max_workers)

    bind(run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    click.echo(f"Running {len(workflows)} workflow(s){' in parallel' if run_parallel else ''}...")

    results: List[dict] = []

    from src.core.engine import Engine  # local import to avoid circulars

    def _run_one(path: Path) -> dict:
        try:
            # Expand multi-doc workflows per file
            if path.suffix.lower() in (".yaml", ".yml"):
                results = []
                for wf in load_workflows_file(path):
                    eng = Engine(settings=settings, interactive_auth=interactive_auth)
                    results.append(eng.run_workflow(wf))
                # If only one, return it; else, merge summarised OK/fail into one
                if len(results) == 1:
                    return results[0]
                ok = all(r.get("ok", True) for r in results)
                return {"ok": ok, "results": results, "workflow": str(path)}
            else:
                eng = Engine(settings=settings, interactive_auth=interactive_auth)
                return eng.run_workflow(load_workflow(path))
        except Exception as e:
            return {"ok": False, "error": str(e), "workflow": str(path)}

    if run_parallel and len(workflows) > 1:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            fut_map = {ex.submit(_run_one, fp): fp for fp in workflows}
            for fut in as_completed(fut_map):
                results.append(fut.result())
    else:
        for fp in workflows:
            results.append(_run_one(fp))

    # Print per-workflow outcome details
    for fp, res in zip(workflows, results):
        if res.get("ok", True):
            click.echo(f"OK  {fp} -> run_dir={res.get('run_dir','-')}")
        else:
            reason = res.get("error", "unknown error")
            err_type = res.get("error_type")
            failed = res.get("failed_step") or {}
            step_desc = ""
            if failed:
                step_desc = f" [step {failed.get('index','?')} {failed.get('action','')} {failed.get('name') or ''}]"
            prefix = f"{err_type}: " if err_type else ""
            click.echo(f"ERR {fp}{step_desc} -> {prefix}{reason}")

    ok_count = sum(1 for r in results if r.get("ok", True))
    fail_count = len(results) - ok_count
    click.echo(f"Done. OK={ok_count}  FAIL={fail_count}")

    if json_out:
        outp = Path(json_out).resolve()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps({"results": results}, indent=2), encoding="utf-8")
        click.echo(f"Wrote summary: {outp}")

    unbind("run_id")
    sys.exit(0 if fail_count == 0 else 1)


def main() -> None:
    cli(prog_name="ui-capture")


if __name__ == "__main__":
    main()

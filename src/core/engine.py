from __future__ import annotations

"""Workflow engine
-------------------
Creates a Playwright browser, runs validated workflow steps, saves images/logs,
and emits a per-run manifest and metadata.json. Structured to be app-agnostic.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from playwright.sync_api import sync_playwright, Browser

from src.utils.config import Settings, get_settings
from src.utils.logger import (
    get_logger,
    log_with_context,
    attach_file_logger,
    detach_file_logger,
)
from src.core.workflow_loader import Workflow, load_workflow, ActionName
from src.core import actions
from src.detection.modal_detector import ModalDetector
from src.detection.overlay_detector import OverlayDetector


@dataclass
class RunContext:
    """Filesystem locations for the current run (images + metadata paths)."""
    run_dir: Path
    images_dir: Path
    meta_path: Path
    manifest_path: Path


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")


def _looks_like_login(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in ("login", "signin", "auth", "session", "accounts.google.com"))


class Engine:
    """Runs workflows against a live browser context and manages artifacts."""
    def __init__(self, settings: Optional[Settings] = None, interactive_auth: bool = True):
        self.settings = settings or get_settings()
        self.log = get_logger(__name__)
        self.interactive_auth = interactive_auth
        self.last_run_base_dir: Optional[Path] = None

    def _storage_state_path_for(self, wf: Workflow) -> Optional[Path]:
        if not getattr(wf, "use_storage_state", False):
            return None
        p = getattr(wf, "storage_state_file", None)
        if p:
            return Path(p)
        site = getattr(wf, "site", getattr(wf, "app", "unknown"))
        return self.settings.STORAGE_STATE_DIR / f"{site}.json"

    def _ensure_auth_interactively(self, browser: Browser, wf: Workflow, start_url: Optional[str] = None) -> Optional[Path]:
        state_path = self._storage_state_path_for(wf)
        if not state_path:
            return None
        self.log.info("Opening interactive login window...")
        context = browser.new_context(viewport={"width": self.settings.VIEWPORT_WIDTH, "height": self.settings.VIEWPORT_HEIGHT})
        page = context.new_page()
        url = start_url or ("https://" + getattr(wf, "site", getattr(wf, "app", "")))
        if not url.startswith("http"):
            url = "https://" + url
        page.goto(url, wait_until="domcontentloaded", timeout=self.settings.PAGE_LOAD_TIMEOUT)
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: A browser window is open.")
        print("Please log in to your workspace in that window.")
        print("When you're fully logged in, press Enter here to continue.")
        input("continue...")
        print("=" * 60 + "\n")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        self.log.info(f"Saved authentication state to: {state_path}")
        try:
            context.close()
        except Exception:
            pass
        return state_path

    def _prepare_run_dirs(self, wf: Workflow) -> RunContext:
        site = getattr(wf, "site", getattr(wf, "app", "unknown"))
        task = getattr(wf, "task", getattr(wf, "name", "task"))
        base = self.settings.OUTPUT_DIR / site / task / _ts()
        base.mkdir(parents=True, exist_ok=True)
        self.last_run_base_dir = base
        return RunContext(
            run_dir=base,
            images_dir=base,
            meta_path=base / "captures.jsonl",
            manifest_path=base / "manifest.json",
        )

    def _write_manifest(self, wf: Workflow, ctx: RunContext) -> None:
        d = {
            "site": getattr(wf, "site", getattr(wf, "app", None)),
            "task": getattr(wf, "task", getattr(wf, "name", None)),
            "run_dir": str(ctx.run_dir),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "steps": len(wf.steps),
        }
        ctx.manifest_path.write_text(json.dumps(d, indent=2), encoding="utf-8")

    def run_workflow(self, wf: Workflow) -> dict:
        """Execute all steps of a workflow and return a small result dict.

        Returns a dict like {"ok": bool, "run_dir": str, ...} and writes
        images + run logs + manifest/metadata to the run directory.
        """
        s = self.settings
        log = self.log
        ctx = self._prepare_run_dirs(wf)
        self._write_manifest(wf, ctx)

        per_run_handler = attach_file_logger(ctx.run_dir / "run.log")
        failed_step_info = None
        try:
            with sync_playwright() as p:
                browser_type = p.chromium if s.BROWSER_TYPE.value == "chromium" else (p.firefox if s.BROWSER_TYPE.value == "firefox" else p.webkit)
                browser = browser_type.launch(headless=s.HEADLESS, slow_mo=s.SLOW_MO)
                try:
                    context_kwargs = s.playwright_context_kwargs()
                    storage_path = self._storage_state_path_for(wf)
                    use_state = bool(getattr(wf, "use_storage_state", False))
                    if use_state and storage_path and storage_path.exists():
                        context_kwargs["storage_state"] = str(storage_path)
                    context = browser.new_context(**context_kwargs)
                    page = context.new_page()

                    if use_state and (not storage_path or not storage_path.exists()) and self.interactive_auth:
                        try:
                            context.close()
                        except Exception:
                            pass
                        try:
                            browser.close()
                        except Exception:
                            pass
                        vis = browser_type.launch(headless=False, slow_mo=max(100, s.SLOW_MO))
                        try:
                            first_goto = None
                            for st in wf.steps:
                                if st.action == ActionName.goto:
                                    first_goto = st.url
                                    break
                            self._ensure_auth_interactively(vis, wf, start_url=first_goto)
                        finally:
                            try:
                                vis.close()
                            except Exception:
                                pass
                        browser = browser_type.launch(headless=s.HEADLESS, slow_mo=s.SLOW_MO)
                        context_kwargs = s.playwright_context_kwargs()
                        if storage_path and storage_path.exists():
                            context_kwargs["storage_state"] = str(storage_path)
                        context = browser.new_context(**context_kwargs)
                        page = context.new_page()

                    log_with = log_with_context(log, site=getattr(wf, "site", getattr(wf, "app", "")), task=getattr(wf, "task", getattr(wf, "name", "")))
                    log_with.info(
                        f"Starting workflow: {getattr(wf, 'site', getattr(wf, 'app', ''))}/{getattr(wf, 'task', getattr(wf, 'name', ''))} (steps={len(wf.steps)})"
                    )

                    # Run-level metadata accumulation
                    # We capture lightweight per-step facts to produce a human-friendly
                    # metadata.json at the end of the run. This does not affect control flow.
                    run_started = datetime.now(timezone.utc)
                    steps_meta: List[Dict[str, Any]] = []
                    prev_url = ""
                    modal_det = ModalDetector()
                    overlay_det = OverlayDetector()

                    for idx, step in enumerate(wf.steps, start=1):
                        step_log = log_with_context(log_with, step_index=idx, action=step.action.value)
                        step_log.info(f"Step {idx}/{len(wf.steps)}: {step.name or step.action.value}")
                        try:
                            # annotate index for screenshot sidecars
                            # (actions._do_screenshot reads this to embed step index)
                            try:
                                setattr(step, "_index", idx)
                            except Exception:
                                pass
                            actions.execute_step(page, step, run_ctx={
                                "images_dir": ctx.images_dir,
                                "meta_path": ctx.meta_path,
                                "settings": s,
                                "run_dir": ctx.run_dir,
                            })
                        except Exception as step_err:
                            is_optional = bool(getattr(step, "optional", False))
                            if is_optional or s.CONTINUE_ON_ERROR:
                                step_log.warning(f"Step failed but optional/continue_on_error set: {step_err}")
                                continue
                            failed_step_info = {"index": idx, "action": step.action.value, "name": step.name}
                            raise

                        if self.interactive_auth and step.action == ActionName.goto:
                            current = page.url
                            if _looks_like_login(current):
                                step_log.info("Detected login/auth page after navigation. Entering interactive login...")
                                try:
                                    context.close()
                                except Exception:
                                    pass
                                try:
                                    browser.close()
                                except Exception:
                                    pass
                                vis = browser_type.launch(headless=False, slow_mo=max(100, s.SLOW_MO))
                                try:
                                    self._ensure_auth_interactively(vis, wf, start_url=current)
                                finally:
                                    try:
                                        vis.close()
                                    except Exception:
                                        pass
                                browser = browser_type.launch(headless=s.HEADLESS, slow_mo=s.SLOW_MO)
                                context_kwargs = s.playwright_context_kwargs()
                                if storage_path and storage_path.exists():
                                    context_kwargs["storage_state"] = str(storage_path)
                                context = browser.new_context(**context_kwargs)
                                page = context.new_page()
                                actions.execute_step(page, step, run_ctx={
                                    "images_dir": ctx.images_dir,
                                    "meta_path": ctx.meta_path,
                                    "settings": s,
                                    "run_dir": ctx.run_dir,
                                })

                        after_wait = getattr(step, "after_wait_ms", None)
                        if after_wait is None:
                            after_wait = getattr(wf, "default_after_wait_ms", None)
                        if after_wait:
                            page.wait_for_timeout(after_wait)

                        # Build per-step metadata
                        # Heuristics: we infer whether URL changed, and whether a modal/overlay is present.
                        # We also associate the most recent *.png at this point as the step's screenshot.
                        now = datetime.now(timezone.utc)
                        cur_url = page.url or ""
                        url_changed = (prev_url != cur_url)
                        has_modal = bool(modal_det.detect_active(page))
                        has_overlay = bool(overlay_det.detect(page))

                        # Guess most recent screenshot file name for the step
                        shot_name = None
                        try:
                            pngs = sorted([p for p in ctx.images_dir.glob('*.png')], key=lambda p: p.stat().st_mtime, reverse=True)
                            shot_name = pngs[0].name if pngs else None
                        except Exception:
                            shot_name = None

                        capture_reason = (
                            'workflow_start' if idx == 1 else ('modal_appeared' if has_modal else ('url_changed' if url_changed else 'step_executed'))
                        )

                        steps_meta.append({
                            "step_number": idx,
                            "screenshot": shot_name,
                            "description": (step.name or step.action.value),
                            "timestamp": now.isoformat().replace("+00:00", "Z"),
                            "url": {
                                "current": cur_url,
                                "has_unique_url": bool(cur_url),
                                "changed_from_previous": url_changed,
                            },
                            "action": {
                                "type": step.action.value,
                                "target": getattr(getattr(step, 'selector', None), 'value', None),
                                "selector": getattr(getattr(step, 'selector', None), 'value', None),
                            },
                            "ui_state": {
                                "has_modal": has_modal,
                                "has_menu": False,
                                "has_dropdown": False,
                                "page_title": (page.title() or ""),
                            },
                            "capture_reason": capture_reason,
                        })
                        prev_url = cur_url

                    # Write run-level metadata.json
                    # This summarizes the run for easy consumption by other agents or tools.
                    try:
                        app_site = getattr(wf, "site", getattr(wf, "app", "")) or ""
                        app_clean = app_site.replace("www.", "")
                        meta_doc: Dict[str, Any] = {
                            "task": getattr(wf, "task", getattr(wf, "name", "task")),
                            "app": (app_clean.split(".")[0] if app_clean else app_clean),
                            "description": getattr(wf, "description", ""),
                            "url": (steps_meta[0]["url"]["current"] if steps_meta else ""),
                            "captured_at": run_started.isoformat().replace("+00:00", "Z"),
                            "total_steps": len(wf.steps),
                            "execution_time_seconds": max(0.0, (datetime.now(timezone.utc) - run_started).total_seconds()),
                            "steps": steps_meta,
                            "statistics": {
                                "url_based_states": sum(1 for srec in steps_meta if srec["url"]["has_unique_url"]),
                                "non_url_states": sum(1 for srec in steps_meta if not srec["url"]["has_unique_url"]),
                                "modal_captures": sum(1 for srec in steps_meta if srec["ui_state"].get("has_modal")),
                                "menu_captures": sum(1 for srec in steps_meta if srec["ui_state"].get("has_menu")),
                                "total_execution_time": f"{max(0.0, (datetime.now(timezone.utc) - run_started).total_seconds()):.1f}s",
                                "average_time_per_step": (f"{( (datetime.now(timezone.utc) - run_started).total_seconds()/ max(1, len(steps_meta)) ):.1f}s"),
                            },
                        }
                        (ctx.run_dir / "metadata.json").write_text(json.dumps(meta_doc, indent=2), encoding="utf-8")
                    except Exception:
                        pass

                    try:
                        context.close()
                    except Exception:
                        pass
                    browser.close()
                    return {"ok": True, "run_dir": str(ctx.run_dir)}
                except Exception as e:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    log.exception("Workflow failed:")
                    result = {"ok": False, "error": str(e), "run_dir": str(ctx.run_dir), "error_type": e.__class__.__name__}
                    if failed_step_info:
                        result["failed_step"] = failed_step_info
                    return result
                finally:
                    try:
                        detach_file_logger(per_run_handler)
                    except Exception:
                        pass
        finally:
            try:
                detach_file_logger(per_run_handler)
            except Exception:
                pass


def run_workflow(workflow: Path | str | Workflow) -> dict:
    if isinstance(workflow, (str, Path)):
        wf = load_workflow(workflow)
    else:
        wf = workflow
    eng = Engine(settings=get_settings())
    return eng.run_workflow(wf)

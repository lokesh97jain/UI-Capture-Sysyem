# src/core/workflow_loader.py
from __future__ import annotations

"""Workflow schema and loader
-----------------------------
Defines the pydantic models for steps/selectors and loads YAML workflows,
including basic normalization for legacy formats and multi-doc files.
"""

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Optional, Union
import os
import re

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic.functional_validators import BeforeValidator

from src.utils.config import get_settings


# ---------- Helpers ----------


def _to_abs_path(p: str | Path) -> Path:
    pth = Path(p) if not isinstance(p, Path) else p
    return pth if pth.is_absolute() else Path.cwd() / pth


AbsPath = Annotated[Path, BeforeValidator(_to_abs_path)]


# ---------- Core enums ----------


class SelectorStrategy(str, Enum):
    css = "css"
    text = "text"
    role = "role"
    xpath = "xpath"


class ActionName(str, Enum):
    goto = "goto"
    click = "click"
    type = "type"
    fill = "fill"
    press = "press"
    hover = "hover"
    wait = "wait"
    wait_for_selector = "wait_for_selector"
    wait_for_network_idle = "wait_for_network_idle"
    select_option = "select_option"
    check = "check"
    uncheck = "uncheck"
    screenshot = "screenshot"
    set_input_files = "set_input_files"
    assert_visible = "assert_visible"
    assert_text = "assert_text"
    assert_url_contains = "assert_url_contains"


# ---------- Shared tiny models ----------


class Selector(BaseModel):
    value: str = Field(..., description="Selector string (css/text/role/xpath)")
    strategy: SelectorStrategy = Field(default=SelectorStrategy.css)
    timeout_ms: int = Field(default=5000, ge=0)

    @field_validator("value")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("selector.value cannot be empty")
        return v


# ---------- Step models (discriminated union by 'action') ----------


class StepBase(BaseModel):
    action: ActionName
    name: Optional[str] = Field(default=None, description="Human-friendly step label")
    after_wait_ms: Optional[int] = Field(default=None, ge=0)
    optional: bool = Field(default=False, description="If true, ignore failure and continue")


class StepGoto(StepBase):
    action: Literal[ActionName.goto]
    url: str = Field(..., description="Absolute URL")

    @field_validator("url")
    @classmethod
    def _url_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("http"):
            raise ValueError("goto.url must be an absolute http(s) URL")
        return v


class StepClick(StepBase):
    action: Literal[ActionName.click]
    selector: Selector


class StepHover(StepBase):
    action: Literal[ActionName.hover]
    selector: Selector


class StepType(StepBase):
    action: Literal[ActionName.type]
    selector: Selector
    text: str = Field(..., description="Text to type")
    clear: bool = Field(default=True)


class StepFill(StepBase):
    action: Literal[ActionName.fill]
    selector: Selector
    text: str = Field(..., description="Text to fill (replaces existing)")


class StepPress(StepBase):
    action: Literal[ActionName.press]
    selector: Optional[Selector] = Field(default=None, description="Optional focused element")
    key: str = Field(..., description="Playwright key string, e.g., 'Enter'")


class StepWait(StepBase):
    action: Literal[ActionName.wait]
    ms: int = Field(..., ge=0)


class StepWaitForSelector(StepBase):
    action: Literal[ActionName.wait_for_selector]
    selector: Selector
    state: str = Field(default="visible")


class StepWaitForNetworkIdle(StepBase):
    action: Literal[ActionName.wait_for_network_idle]
    idle_ms: int = Field(default=500)


class StepSelectOption(StepBase):
    action: Literal[ActionName.select_option]
    selector: Selector
    value: Optional[str] = None
    label: Optional[str] = None
    index: Optional[int] = None


class StepCheck(StepBase):
    action: Literal[ActionName.check]
    selector: Selector


class StepUncheck(StepBase):
    action: Literal[ActionName.uncheck]
    selector: Selector


class StepSetInputFiles(StepBase):
    action: Literal[ActionName.set_input_files]
    selector: Selector
    files: list[AbsPath] = Field(..., description="List of local files to upload")


class StepScreenshot(StepBase):
    action: Literal[ActionName.screenshot]
    name: str = Field(..., description="Base filename (no extension)")
    full_page: Optional[bool] = Field(default=None)
    selector: Optional[Selector] = Field(default=None, description="Element to clip (if not full_page)")


class StepAssertVisible(StepBase):
    action: Literal[ActionName.assert_visible]
    selector: Selector
    state: Literal["visible", "hidden", "attached", "detached"] = Field(default="visible")


class StepAssertText(StepBase):
    action: Literal[ActionName.assert_text]
    selector: Selector
    expect: str = Field(..., description="Expected substring or regex literal /.../")
    regex: bool = Field(default=False, description="Treat expect as regex without surrounding //")


class StepAssertUrlContains(StepBase):
    action: Literal[ActionName.assert_url_contains]
    text: str = Field(..., description="Substring that must appear in page URL")


Step = Union[
    StepGoto,
    StepClick,
    StepHover,
    StepType,
    StepFill,
    StepPress,
    StepWait,
    StepWaitForSelector,
    StepWaitForNetworkIdle,
    StepSelectOption,
    StepCheck,
    StepUncheck,
    StepSetInputFiles,
    StepScreenshot,
    StepAssertVisible,
    StepAssertText,
    StepAssertUrlContains,
]


# ---------- Workflow model ----------


class Workflow(BaseModel):
    version: str = Field(default="1")
    site: str = Field(..., description="Site key folder, e.g., 'linear.app'")
    task: str = Field(..., description="Task name, e.g., 'create_project'")
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    use_storage_state: bool = Field(default=True, description="Use persisted login session")
    storage_state_file: Optional[AbsPath] = Field(default=None, description="Override path to storage state")

    steps: list[Step]

    default_after_wait_ms: Optional[int] = Field(default=None, ge=0)
    output_dir: Optional[AbsPath] = None

    @field_validator("site")
    @classmethod
    def _site_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("site cannot be empty")
        return v

    @field_validator("task")
    @classmethod
    def _task_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("task cannot be empty")
        return v

    @field_validator("storage_state_file", mode="after")
    @classmethod
    def _default_storage_state(cls, v: Optional[Path], info):
        if v:
            return v
        settings = get_settings()
        site = info.data.get("site")
        if not site:
            return None
        return settings.STORAGE_STATE_DIR / f"{site}.json"


# ---------- Public API ----------


def load_workflow(path: Path | str) -> Workflow:
    wf_path = Path(path)
    if not wf_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {wf_path}")

    def _infer_site_task(p: Path) -> tuple[str, str]:
        # Try to infer site from parent folder like workflows/<site>/<file>.yaml
        site = p.parent.name if p.parent else "unknown"
        task = p.stem
        return site, task

    def _normalize_legacy(data: dict, p: Path) -> dict:
        # If clearly already in new schema, return as-is
        if "site" in data and "task" in data and isinstance(data.get("steps"), list):
            return data

        site, task = _infer_site_task(p)
        steps: list[dict] = []
        for s in data.get("steps", []) or []:
            if not isinstance(s, dict):
                continue
            action = s.get("action")
            wait_after = s.get("wait")
            shot = s.get("screenshot")
            sel_timeout = int(s.get("timeout_ms", 15000))
            sel_obj = ({"value": s.get("selector", ""), "strategy": "css", "timeout_ms": sel_timeout}
                       if s.get("selector") else None)

            def _add_after_wait(step_obj: dict) -> dict:
                if isinstance(wait_after, int) and wait_after >= 0:
                    step_obj["after_wait_ms"] = wait_after
                return step_obj

            if action == "navigate" or action == "goto":
                steps.append(_add_after_wait({
                    "action": "goto",
                    "url": s.get("url", ""),
                    "name": s.get("name"),
                }))
            elif action == "click":
                if sel_obj:
                    steps.append({"action": "wait_for_selector", "selector": sel_obj, "state": "visible"})
                steps.append(_add_after_wait({
                    "action": "click",
                    "selector": sel_obj or {"value": "", "strategy": "css", "timeout_ms": sel_timeout},
                    "name": s.get("name"),
                }))
            elif action == "hover":
                if sel_obj:
                    steps.append({"action": "wait_for_selector", "selector": sel_obj, "state": "visible"})
                steps.append(_add_after_wait({
                    "action": "hover",
                    "selector": sel_obj or {"value": "", "strategy": "css", "timeout_ms": sel_timeout},
                    "name": s.get("name"),
                }))
            elif action == "type" or action == "fill":
                key = "type" if action == "type" else "fill"
                if sel_obj:
                    steps.append({"action": "wait_for_selector", "selector": sel_obj, "state": "visible"})
                o = {
                    "action": key,
                    "selector": sel_obj or {"value": "", "strategy": "css", "timeout_ms": sel_timeout},
                    "text": s.get("text", ""),
                    "name": s.get("name"),
                }
                if key == "type":
                    o["clear"] = True
                steps.append(_add_after_wait(o))
            elif action == "press":
                steps.append(_add_after_wait({
                    "action": "press",
                    "key": s.get("key", "Enter"),
                    "selector": ({"value": s.get("selector", ""), "strategy": "css"} if s.get("selector") else None),
                    "name": s.get("name"),
                }))
            elif action == "wait":
                steps.append({
                    "action": "wait",
                    "ms": int(s.get("ms", s.get("time", s.get("wait", 0))) or 0),
                    "name": s.get("name"),
                })
            # Ignore unknown actions silently for legacy mode

            # Optional screenshot after this step
            if shot:
                steps.append({
                    "action": "screenshot",
                    "name": str(shot),
                })

        return {
            "version": data.get("version", "1"),
            "site": data.get("site", site),
            "task": data.get("task", task),
            "description": data.get("description"),
            "use_storage_state": data.get("use_storage_state", True),
            "steps": steps,
        }

    try:
        raw = wf_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError("Workflow YAML must define a mapping/object at the top level.")
        data = _normalize_legacy(data, wf_path)

        # Environment variable substitution for all string fields
        def _subst_env(obj):
            if isinstance(obj, str):
                def repl(m):
                    key = m.group(1)
                    return os.environ.get(key, m.group(0))
                return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, obj)
            if isinstance(obj, list):
                return [_subst_env(x) for x in obj]
            if isinstance(obj, dict):
                return {k: _subst_env(v) for k, v in obj.items()}
            return obj

        data = _subst_env(data)
        wf = Workflow.model_validate(data)
        return wf
    except ValidationError as ve:
        lines = [f"Invalid workflow '{wf_path}':"]
        for e in ve.errors():
            loc = ".".join(str(p) for p in e.get("loc", []))
            msg = e.get("msg", "invalid value")
            lines.append(f"  - {loc}: {msg}")
        raise ValueError("\n".join(lines)) from ve
    except yaml.YAMLError as ye:
        raise ValueError(f"YAML parse error in {wf_path}: {ye}") from ye


def load_workflows_file(path: Path | str) -> list[Workflow]:
    """Load one or more workflows from a YAML file (supports multi-document)."""
    wf_path = Path(path)
    if not wf_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {wf_path}")
    try:
        raw = wf_path.read_text(encoding="utf-8")
        docs = list(yaml.safe_load_all(raw))
        out: list[Workflow] = []
        for idx, data in enumerate(docs, start=1):
            if data is None:
                continue
            if not isinstance(data, dict):
                raise ValueError(f"Document {idx} in {wf_path} must be a mapping/object.")
            try:
                norm = locals().get("_normalize_legacy")(data, wf_path)  # use inner helper
            except Exception:
                # Fallback if helper is not present for some reason
                norm = data
            # Environment variable substitution
            def _subst_env(obj):
                if isinstance(obj, str):
                    def repl(m):
                        key = m.group(1)
                        return os.environ.get(key, m.group(0))
                    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, obj)
                if isinstance(obj, list):
                    return [_subst_env(x) for x in obj]
                if isinstance(obj, dict):
                    return {k: _subst_env(v) for k, v in obj.items()}
                return obj
            norm = _subst_env(norm)
            try:
                out.append(Workflow.model_validate(norm))
            except ValidationError as ve:
                lines = [f"Invalid workflow '{wf_path}' (document {idx}):"]
                for e in ve.errors():
                    loc = ".".join(str(p) for p in e.get("loc", []))
                    msg = e.get("msg", "invalid value")
                    lines.append(f"  - {loc}: {msg}")
                raise ValueError("\n".join(lines)) from ve
        if not out:
            raise ValueError(f"No valid workflow documents found in {wf_path}")
        return out
    except yaml.YAMLError as ye:
        raise ValueError(f"YAML parse error in {wf_path}: {ye}") from ye


__all__ = [
    "SelectorStrategy",
    "ActionName",
    "Selector",
    "Workflow",
    "load_workflow",
    "load_workflows_file",
]


# ---------- Optional loader utility ----------


class WorkflowLoader:
    def load_directory(
        self,
        root: Path,
        *,
        recursive: bool = True,
        filter_app: Optional[str] = None,
    ) -> list[Workflow]:
        def _find_yaml_files(r: Path) -> list[Path]:
            if recursive:
                return sorted(list(r.rglob("*.yaml")) + list(r.rglob("*.yml")))
            return sorted(list(r.glob("*.yaml")) + list(r.glob("*.yml")))

        workflows: list[Workflow] = []
        for fp in _find_yaml_files(root):
            try:
                for wf in load_workflows_file(fp):
                    if filter_app:
                        site = getattr(wf, "site", getattr(wf, "app", ""))
                        if site != filter_app:
                            continue
                    workflows.append(wf)
            except Exception:
                continue
        return workflows

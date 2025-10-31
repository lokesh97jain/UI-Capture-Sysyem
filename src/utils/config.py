# src/utils/config.py
from __future__ import annotations

import functools
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------- Enums ----------

class BrowserType(str, Enum):
    chromium = "chromium"
    firefox = "firefox"
    webkit = "webkit"


class ScreenshotFormat(str, Enum):
    png = "png"
    jpeg = "jpeg"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ---------- Settings ----------

class Settings(BaseSettings):
    """
    Central configuration for the UI Capture System.

    Values load in this order of precedence:
      1) Environment variables
      2) .env file in project root
      3) Defaults below
    """

    # ---- Browser configuration ----
    HEADLESS: bool = Field(default=True, description="Run the browser headless")
    BROWSER_TYPE: BrowserType = Field(default=BrowserType.chromium, description="Playwright browser")
    VIEWPORT_WIDTH: int = Field(default=1366, ge=320, le=7680)
    VIEWPORT_HEIGHT: int = Field(default=768, ge=320, le=4320)
    SLOW_MO: int = Field(default=0, ge=0, description="Slow down actions (ms) for debugging")
    USER_AGENT: Optional[str] = Field(default=None)

    # ---- Capture settings ----
    OUTPUT_DIR: Path = Field(default=Path("./datasets"))
    WORKFLOWS_DIR: Path = Field(default=Path("./workflows"))
    STORAGE_STATE_DIR: Path = Field(default=Path("./storage_state"))

    PAGE_LOAD_TIMEOUT: int = Field(default=60000, ge=1000)
    DEFAULT_ACTION_WAIT: int = Field(default=800, ge=0)
    ANIMATION_WAIT: int = Field(default=1000, ge=0)

    SCREENSHOT_FORMAT: ScreenshotFormat = Field(default=ScreenshotFormat.png)
    SCREENSHOT_QUALITY: int = Field(default=90, ge=1, le=100)
    FULL_PAGE_SCREENSHOT: bool = Field(default=True)
    SELECTOR_TIMEOUT_MS: int = Field(default=15000, ge=1000)

    # ---- Detection settings ----
    DETECT_MODALS: bool = True
    DETECT_OVERLAYS: bool = True
    DETECT_ANIMATIONS: bool = True
    NETWORK_IDLE_TIMEOUT: int = Field(default=2000, ge=0)

    # ---- Retry & error handling ----
    MAX_RETRIES: int = Field(default=3, ge=0)
    RETRY_DELAY: int = Field(default=1000, ge=0)
    CONTINUE_ON_ERROR: bool = Field(default=False)

    # ---- Logging ----
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO)
    LOG_TO_FILE: bool = Field(default=False)
    LOG_FILE: Path = Field(default=Path("./ui-capture.log"))
    COLORIZED_OUTPUT: bool = Field(default=True)
    DEBUG_MODE: bool = Field(default=False)

    # ---- Performance / Optimization ----
    OPTIMIZE_IMAGES: bool = Field(default=False)
    MAX_IMAGE_SIZE_KB: int = Field(default=0, ge=0, description="0 = no limit")
    PARALLEL_EXECUTION: bool = Field(default=False)
    MAX_WORKERS: int = Field(default=3, ge=1)

    # ---- Tracing / Diagnostics ----
    SAVE_TRACES: bool = Field(default=False)
    TRACE_DIR: Path = Field(default=Path("./traces"))

    # ---- Proxies ----
    PROXY_SERVER: Optional[str] = None
    PROXY_USERNAME: Optional[str] = None
    PROXY_PASSWORD: Optional[str] = None

    # ---- App-specific (optional) ----
    LINEAR_WORKSPACE: Optional[str] = None
    NOTION_WORKSPACE: Optional[str] = None
    GITHUB_ORG: Optional[str] = None
    GITHUB_REPO: Optional[str] = None

    # ---- Derived/resolved paths (computed) ----
    project_root: Path = Field(default_factory=lambda: Path.cwd())

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignore unknown envs to keep things flexible
    )

    # Normalize path-like fields to absolute paths
    @field_validator(
        "OUTPUT_DIR",
        "WORKFLOWS_DIR",
        "STORAGE_STATE_DIR",
        "LOG_FILE",
        "TRACE_DIR",
        mode="before",
    )
    @classmethod
    def _coerce_to_path(cls, v):
        if isinstance(v, Path):
            return v
        return Path(str(v)) if v is not None else v

    @field_validator("OUTPUT_DIR", "WORKFLOWS_DIR", "STORAGE_STATE_DIR", "TRACE_DIR", mode="after")
    @classmethod
    def _absolutize_dirs(cls, v: Path, info):
        # Make directories relative to CWD absolute for consistency
        return v if v.is_absolute() else Path.cwd() / v

    @field_validator("LOG_FILE", mode="after")
    @classmethod
    def _absolutize_log_file(cls, v: Path, info):
        return v if v.is_absolute() else Path.cwd() / v

    # Guard: JPEG requires a quality value
    @field_validator("SCREENSHOT_QUALITY")
    @classmethod
    def _quality_for_jpeg(cls, quality: int):
        # We keep quality always available; engine can ignore for PNG
        return quality

    # Safety: cap viewport to reasonable min/max (already constrained, double-check)
    @field_validator("VIEWPORT_WIDTH", "VIEWPORT_HEIGHT")
    @classmethod
    def _viewport_bounds(cls, val: int):
        return max(320, min(val, 10000))

    def ensure_dirs(self) -> None:
        """Create required directories (idempotent)."""
        for p in {self.OUTPUT_DIR, self.STORAGE_STATE_DIR, self.TRACE_DIR, self.LOG_FILE.parent}:
            p.mkdir(parents=True, exist_ok=True)

    # Convenience: Playwright launch options dict
    def playwright_launch_kwargs(self) -> dict:
        kwargs = {
            "headless": self.HEADLESS,
            "slow_mo": self.SLOW_MO,
        }
        # proxy
        if self.PROXY_SERVER:
            proxy = {"server": self.PROXY_SERVER}
            if self.PROXY_USERNAME and self.PROXY_PASSWORD:
                proxy["username"] = self.PROXY_USERNAME
                proxy["password"] = self.PROXY_PASSWORD
            kwargs["proxy"] = proxy
        return kwargs

    # Convenience: Playwright new_context kwargs
    def playwright_context_kwargs(self) -> dict:
        viewport = {"width": self.VIEWPORT_WIDTH, "height": self.VIEWPORT_HEIGHT}
        ctx = {"viewport": viewport}
        if self.USER_AGENT:
            ctx["user_agent"] = self.USER_AGENT
        return ctx


# --------- Public accessor (memoized) ---------

@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Load and cache settings once per process.
    Call `get_settings.cache_clear()` if you need to reload after changing env.
    """
    s = Settings()
    s.ensure_dirs()
    return s


# --------- Lightweight DTO for other modules (optional) ---------

class Paths(BaseModel):
    workflows: Path
    datasets: Path
    storage_state: Path
    traces: Path
    log_file: Path

    @classmethod
    def from_settings(cls, s: Settings) -> "Paths":
        return cls(
            workflows=s.WORKFLOWS_DIR,
            datasets=s.OUTPUT_DIR,
            storage_state=s.STORAGE_STATE_DIR,
            traces=s.TRACE_DIR,
            log_file=s.LOG_FILE,
        )

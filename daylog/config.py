"""Configuration loading and defaults.

Reads config.toml from the project root (next to config.example.toml) if present,
falling back to built-in defaults. Access via `get_config()`.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:  # tomllib is stdlib on 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path.home() / ".daylog"


def _config_dir() -> Path:
    """Directory that holds config.toml.

    In a normal source checkout this is the project root (next to config.example.toml).
    In a packaged build (PyInstaller sets ``sys.frozen``) the bundle is not a reliable
    writable location, so config lives in a stable per-user directory instead.
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        d = Path(base) / "daylog"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return PROJECT_ROOT


CONFIG_DIR = _config_dir()


@dataclass
class StorageConfig:
    data_dir: Path = DEFAULT_DATA_DIR
    screenshot_retention_days: int = 14


@dataclass
class CaptureConfig:
    interval_seconds: int = 90
    switch_debounce_seconds: int = 4
    idle_threshold_seconds: int = 120
    jpeg_quality: int = 75
    max_width: int = 2560
    exclude_apps: list[str] = field(default_factory=lambda: ["1Password", "Bitwarden", "KeePass"])
    hotkey: str = "ctrl+alt+s"   # global hotkey for a manual (badged) screenshot


@dataclass
class WatcherConfig:
    """The built-in activity watcher (foreground window + idle + browser tab)."""
    # How often to tail shell history. The window itself is sampled every loop (2s).
    poll_seconds: int = 30
    # Read the active tab's URL from the browser address bar via UI Automation.
    track_urls: bool = True


@dataclass
class SegmentConfig:
    idle_gap_seconds: int = 180
    title_change_ratio: float = 0.5


@dataclass
class ObsidianConfig:
    vault_path: Path | None = None
    notes_subdir: str = "Activity"
    attachments_subdir: str = "Activity/attachments"
    embed_all_screenshots: bool = True
    embed_max_width: int = 1280
    # If vault_path is unset or no longer exists, find it in Obsidian's own registry.
    auto_detect: bool = True
    vault_name_hint: str = "Activity Tracker"


@dataclass
class AIConfig:
    # "ollama" | "claude" | "lmstudio" | "openai" (any OpenAI-compatible server)
    provider: str = "ollama"
    ollama_model: str = "llama3.1"
    claude_model: str = "claude-opus-4-8"
    # LM Studio (local, OpenAI-compatible). Empty model = auto-detect the loaded model.
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = ""
    # Generic OpenAI-compatible / OpenAI. Key read from OPENAI_API_KEY.
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"


@dataclass
class TerminalConfig:
    enabled: bool = True
    powershell_history: Path | None = None


@dataclass
class ClaudeCodeConfig:
    # Use Claude Code session transcripts (prompts + actions) as the primary work signal.
    enabled: bool = True
    logs_dir: Path | None = None   # defaults to ~/.claude/projects


@dataclass
class Config:
    storage: StorageConfig = field(default_factory=StorageConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    segment: SegmentConfig = field(default_factory=SegmentConfig)
    obsidian: ObsidianConfig = field(default_factory=ObsidianConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    terminal: TerminalConfig = field(default_factory=TerminalConfig)
    claude_code: ClaudeCodeConfig = field(default_factory=ClaudeCodeConfig)

    @property
    def db_path(self) -> Path:
        return self.storage.data_dir / "daylog.db"

    @property
    def screenshots_dir(self) -> Path:
        return self.storage.data_dir / "screenshots"


def _path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


# Keys whose values are filesystem paths, regardless of current default value.
_PATH_KEYS = {"data_dir", "vault_path", "powershell_history", "logs_dir"}


def _merge(section: dict, target) -> None:
    """Copy known keys from a TOML section dict onto a dataclass instance."""
    for key, value in section.items():
        if not hasattr(target, key):
            continue  # ignore unknown keys rather than crash
        if key in _PATH_KEYS:
            value = _path(value)
        setattr(target, key, value)


def _obsidian_registry_file() -> Path | None:
    """Locate Obsidian's obsidian.json (lists every known vault + its path) per OS."""
    appdata = os.environ.get("APPDATA")
    candidates = []
    if appdata:
        candidates.append(Path(appdata) / "obsidian" / "obsidian.json")
    candidates += [
        Path.home() / "Library/Application Support/obsidian/obsidian.json",  # macOS
        Path.home() / ".config/obsidian/obsidian.json",                       # Linux
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def auto_detect_vault(name_hint: str | None = None) -> Path | None:
    """Pick a vault from Obsidian's registry: prefer one whose folder name matches the
    hint, otherwise the most-recently-opened. Only returns paths that still exist."""
    reg = _obsidian_registry_file()
    if not reg:
        return None
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except Exception:
        return None
    entries = [
        (Path(v["path"]), v.get("ts", 0))
        for v in data.get("vaults", {}).values()
        if v.get("path") and Path(v["path"]).exists()
    ]
    if not entries:
        return None
    if name_hint:
        matches = [e for e in entries if name_hint.lower() in e[0].name.lower()]
        if matches:
            return max(matches, key=lambda e: e[1])[0]
    return max(entries, key=lambda e: e[1])[0]


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    config_file = path or (CONFIG_DIR / "config.toml")
    if config_file.exists():
        with open(config_file, "rb") as fh:
            data = tomllib.load(fh)
        _merge(data.get("storage", {}), cfg.storage)
        _merge(data.get("capture", {}), cfg.capture)
        _merge(data.get("watcher", {}), cfg.watcher)
        _merge(data.get("segment", {}), cfg.segment)
        _merge(data.get("obsidian", {}), cfg.obsidian)
        _merge(data.get("ai", {}), cfg.ai)
        _merge(data.get("terminal", {}), cfg.terminal)
        _merge(data.get("claude_code", {}), cfg.claude_code)

    # data_dir may arrive as a string via _merge; normalize.
    if not isinstance(cfg.storage.data_dir, Path):
        cfg.storage.data_dir = Path(str(cfg.storage.data_dir)).expanduser()

    cfg.storage.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.screenshots_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the Obsidian vault: use the configured path if it still exists, otherwise
    # auto-detect from Obsidian's registry (so moving the vault just works).
    vp = cfg.obsidian.vault_path
    if cfg.obsidian.auto_detect and (vp is None or not vp.exists()):
        detected = auto_detect_vault(cfg.obsidian.vault_name_hint)
        if detected:
            cfg.obsidian.vault_path = detected

    return cfg


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()


def _toml_str(v) -> str:
    return '"' + str(v).replace("\\", "/").replace('"', '\\"') + '"'


def _toml_list(items) -> str:
    return "[" + ", ".join(_toml_str(i) for i in items) + "]"


def save_config(cfg: Config, path: Path | None = None) -> Path:
    """Write the full config back to config.toml (used by the Settings UI)."""
    config_file = path or (CONFIG_DIR / "config.toml")
    o = cfg.obsidian
    lines = [
        "# daylog configuration (written by the Settings UI).",
        "",
        "[storage]",
        f"data_dir = {_toml_str(cfg.storage.data_dir)}",
        f"screenshot_retention_days = {cfg.storage.screenshot_retention_days}",
        "",
        "[capture]",
        f"interval_seconds = {cfg.capture.interval_seconds}",
        f"switch_debounce_seconds = {cfg.capture.switch_debounce_seconds}",
        f"idle_threshold_seconds = {cfg.capture.idle_threshold_seconds}",
        f"jpeg_quality = {cfg.capture.jpeg_quality}",
        f"max_width = {cfg.capture.max_width}",
        f"exclude_apps = {_toml_list(cfg.capture.exclude_apps)}",
        f"hotkey = {_toml_str(cfg.capture.hotkey)}",
        "",
        "[watcher]",
        f"poll_seconds = {cfg.watcher.poll_seconds}",
        f"track_urls = {str(cfg.watcher.track_urls).lower()}",
        "",
        "[segment]",
        f"idle_gap_seconds = {cfg.segment.idle_gap_seconds}",
        f"title_change_ratio = {cfg.segment.title_change_ratio}",
        "",
        "[obsidian]",
        *([f"vault_path = {_toml_str(o.vault_path)}"] if o.vault_path else []),
        f"notes_subdir = {_toml_str(o.notes_subdir)}",
        f"attachments_subdir = {_toml_str(o.attachments_subdir)}",
        f"embed_all_screenshots = {str(o.embed_all_screenshots).lower()}",
        f"embed_max_width = {o.embed_max_width}",
        f"auto_detect = {str(o.auto_detect).lower()}",
        f"vault_name_hint = {_toml_str(o.vault_name_hint)}",
        "",
        "[ai]",
        f"provider = {_toml_str(cfg.ai.provider)}",
        f"ollama_model = {_toml_str(cfg.ai.ollama_model)}",
        f"claude_model = {_toml_str(cfg.ai.claude_model)}",
        f"lmstudio_base_url = {_toml_str(cfg.ai.lmstudio_base_url)}",
        f"lmstudio_model = {_toml_str(cfg.ai.lmstudio_model)}",
        f"openai_base_url = {_toml_str(cfg.ai.openai_base_url)}",
        f"openai_model = {_toml_str(cfg.ai.openai_model)}",
        "",
        "[terminal]",
        f"enabled = {str(cfg.terminal.enabled).lower()}",
        "",
        "[claude_code]",
        f"enabled = {str(cfg.claude_code.enabled).lower()}",
        *([f"logs_dir = {_toml_str(cfg.claude_code.logs_dir)}"]
          if cfg.claude_code.logs_dir else []),
        "",
    ]
    config_file.write_text("\n".join(lines), encoding="utf-8")
    get_config.cache_clear()
    return config_file


def anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def openai_api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")

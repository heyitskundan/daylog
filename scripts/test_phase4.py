"""Validate P4 control-center: settings render+save, control bar, capture control, launcher."""

import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from daylog import autostart, db
import daylog.config as C
from daylog.config import Config, ObsidianConfig, StorageConfig, load_config, save_config
from daylog.web import render_day, render_settings

# 1. Settings page renders all controls.
cfg = Config(storage=StorageConfig(data_dir=Path(tempfile.mkdtemp())),
             obsidian=ObsidianConfig(vault_path=Path("D:/Vault/AT"), auto_detect=False))
status = {"watcher": True, "watcher_detail": "Code.exe · browser URLs on",
          "ocr": True, "ocr_detail": "windows engine",
          "ai": True, "ai_detail": "ready", "vault": True, "vault_detail": "D:/Vault/AT"}
html = render_settings(cfg, status, autostart_on=False)
for needle in ['name="provider"', 'name="vault_path"', 'name="hotkey"', 'name="data_dir"',
               "Save settings", "Launch on login", "Activity watcher", "Screenshot text (OCR)"]:
    assert needle in html, needle
assert "ActivityWatch" not in html, "the settings page should not mention ActivityWatch any more"
print("OK: settings page renders watcher/OCR/provider/vault/data-dir/hotkey/autostart")

# 2. save_config round-trips (incl. Windows-style vault path).
cfg.ai.provider = "lmstudio"
cfg.capture.hotkey = "ctrl+shift+9"
cfg.storage.screenshot_retention_days = 21
tmp_cfg = Path(tempfile.mkdtemp()) / "config.toml"
save_config(cfg, tmp_cfg)
reloaded = load_config(tmp_cfg)
assert reloaded.ai.provider == "lmstudio", reloaded.ai.provider
assert reloaded.capture.hotkey == "ctrl+shift+9", reloaded.capture.hotkey
assert reloaded.storage.screenshot_retention_days == 21
assert str(reloaded.obsidian.vault_path).replace("\\", "/").endswith("Vault/AT")
print("OK: save_config writes valid TOML and round-trips")

# 3. Control bar reflects capture state.
day = {"date": "2026-06-24", "segments": [], "by_app": [("Code.exe", 300)],
       "total_active": 300, "shots": [], "tasks": [], "entries": [], "lede": ""}
# segments non-empty so it doesn't hit the nothing-captured early return
day["segments"] = [{"active_secs": 300, "primary_app": "Code.exe"}]
assert 'action="/api/capture/stop"' in render_day(day, capture_running=True)
assert 'action="/api/capture/start"' in render_day(day, capture_running=False)
assert "managed by the tray" in render_day(day, capture_running=None)
print("OK: capture chip shows start/stop/tray states")

# 4. Live server: capture start/stop via a fake controller (no real capture).
class FakeController:
    def __init__(self): self._on = False
    @property
    def running(self): return self._on
    def start(self): self._on = True
    def stop(self): self._on = False

cfg2 = Config(storage=StorageConfig(data_dir=Path(tempfile.mkdtemp())))
C.get_config = lambda: cfg2
db.init_db(cfg2.db_path)
fake = FakeController()
from daylog import web
server, url = web.serve_background(port=8791, controller=fake)
try:
    time.sleep(0.3)
    def post(path):
        req = urllib.request.Request(url.rstrip("/") + path,
                                     data=urllib.parse.urlencode({"date": "2026-06-24"}).encode(),
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    assert post("/api/capture/start") == 200 and fake.running is True
    assert post("/api/capture/stop") == 200 and fake.running is False
    print("OK: dashboard Start/Stop capture controls the controller")
finally:
    server.shutdown()

# 5. Launcher generation (into a temp dir, not the real project root).
autostart.PROJECT_ROOT = Path(tempfile.mkdtemp())
p = autostart.write_launcher()
assert p.exists() and "daylog.cli tray" in p.read_text(encoding="utf-8")
print(f"OK: launcher generated ({p.name})")

print("\nALL P4 CONTROL-CENTER CHECKS PASSED")

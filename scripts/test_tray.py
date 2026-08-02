"""Validate the tray building blocks without launching the GUI (no .run())."""

import tempfile
import time
import urllib.request
from pathlib import Path

from daylog import capture, icon
from daylog.config import Config, StorageConfig
from daylog.tray import CaptureController

# 1. Icon renders.
img = icon.render(64)
assert img.size == (64, 64), img.size
print("OK: icon renders 64x64")

# 2. CaptureController start/stop, with a fake capture loop (no real screenshots / AW).
def fake_run_capture(cfg=None, stop_event=None):
    while stop_event is not None and not stop_event.is_set():
        time.sleep(0.01)

capture.run_capture = fake_run_capture  # monkeypatch

cfg = Config(storage=StorageConfig(data_dir=Path(tempfile.mkdtemp())))
ctl = CaptureController(cfg)
assert not ctl.running
ctl.start()
time.sleep(0.1)
assert ctl.running, "controller should report running after start"
ctl.stop()
assert not ctl.running, "controller should stop"
print("OK: CaptureController start/stop")

# 3. Background dashboard server serves then shuts down.
from daylog import db, web

db.init_db(cfg.db_path)
# point get_config-less serve_background at our temp db via config cache
import daylog.config as C
C.get_config.cache_clear()
# serve_background uses get_config(); patch it to our cfg for the test
C.get_config = lambda: cfg
server, url = web.serve_background(port=8788)
try:
    time.sleep(0.3)
    with urllib.request.urlopen(url, timeout=5) as r:
        assert r.status == 200, r.status
    print("OK: background dashboard responds at", url)
finally:
    server.shutdown()

# 4. pystray imports and an Icon + Menu can be constructed (without running it).
import pystray

menu = pystray.Menu(
    pystray.MenuItem(lambda i: "Start capture", lambda i, it: None),
    pystray.MenuItem("Quit", lambda i, it: None),
)
ti = pystray.Icon("daylog-test", icon.render(32), "daylog", menu)
assert ti is not None
print("OK: pystray Icon + Menu construct")

print("\nALL TRAY BUILDING-BLOCK CHECKS PASSED")

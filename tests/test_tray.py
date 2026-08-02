import time
from pathlib import Path

from daylog import capture, icon
from daylog.config import Config, StorageConfig
from daylog.tray import CaptureController


def test_icon_render_produces_expected_size():
    img = icon.render(64)
    assert img.size == (64, 64)


def test_capture_controller_start_stop_cycle(tmp_path, monkeypatch):
    def fake_run_capture(cfg=None, stop_event=None):
        while stop_event is not None and not stop_event.is_set():
            time.sleep(0.01)

    monkeypatch.setattr(capture, "run_capture", fake_run_capture)

    cfg = Config(storage=StorageConfig(data_dir=tmp_path))
    ctl = CaptureController(cfg)
    assert not ctl.running

    ctl.start()
    time.sleep(0.1)
    assert ctl.running

    ctl.stop()
    assert not ctl.running


def test_capture_controller_start_is_a_no_op_when_already_running(tmp_path, monkeypatch):
    def fake_run_capture(cfg=None, stop_event=None):
        while stop_event is not None and not stop_event.is_set():
            time.sleep(0.01)

    monkeypatch.setattr(capture, "run_capture", fake_run_capture)

    cfg = Config(storage=StorageConfig(data_dir=tmp_path))
    ctl = CaptureController(cfg)
    ctl.start()
    time.sleep(0.05)
    first_thread = ctl._thread
    ctl.start()
    assert ctl._thread is first_thread
    ctl.stop()

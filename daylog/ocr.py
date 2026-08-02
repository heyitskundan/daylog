"""Pluggable OCR.

Tries backends in order of preference and caches the first that works:
  1. Windows.Media.Ocr  - the engine built into Windows. Nothing to install, no model
                          download, runs offline. This is the default on Windows.
  2. RapidOCR (rapidocr-onnxruntime) - cross-platform fallback, no system binary
  3. pytesseract        - needs the Tesseract binary on PATH

If none is available, OCR is a no-op: screenshots are still captured and the pipeline runs;
ocr_text just stays empty. Install a backend later and run `daylog backfill-ocr`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Callable, Optional

_backend: Optional[Callable[[str], str]] = None
_backend_name = "none"
_resolved = False
_lock = threading.Lock()


def _windows_ocr() -> Optional[Callable[[str], str]]:
    """The OS OCR engine. Async WinRT API, driven synchronously per image."""
    try:
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except Exception:
        return None
    if OcrEngine.try_create_from_user_profile_languages() is None:
        return None      # no OCR language pack for this user

    async def _recognize(path: str) -> str:
        # WinRT wants a real Windows path (backslashes, absolute) or it raises "path invalid".
        file = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
        stream = await file.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        result = await engine.recognize_async(bitmap)
        return result.text or ""

    def _run(path: str) -> str:
        return asyncio.run(_recognize(path))

    return _run


def _resolve_backend() -> Optional[Callable[[str], str]]:
    global _backend, _backend_name, _resolved
    with _lock:
        if _resolved:
            return _backend
        _resolved = True

        # Windows' own OCR engine — preferred: nothing to install.
        win = _windows_ocr()
        if win is not None:
            _backend, _backend_name = win, "windows"
            return _backend

        # RapidOCR
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            engine = RapidOCR()

            def _rapid(path: str) -> str:
                result, _ = engine(path)
                if not result:
                    return ""
                return "\n".join(line[1] for line in result)

            _backend, _backend_name = _rapid, "rapidocr"
            return _backend
        except Exception:
            pass

        # Tesseract
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            def _tess(path: str) -> str:
                return pytesseract.image_to_string(Image.open(path))

            _backend, _backend_name = _tess, "tesseract"
            return _backend
        except Exception:
            pass

        _backend = None
        return None


def available() -> bool:
    return _resolve_backend() is not None


def backend_name() -> str:
    """Which engine is in use: windows | rapidocr | tesseract | none."""
    _resolve_backend()
    return _backend_name


def ocr_image(path: str) -> str:
    backend = _resolve_backend()
    if backend is None:
        return ""
    try:
        return backend(path).strip()
    except Exception:
        return ""


def run_worker(get_conn, stop_event: threading.Event, poll: float = 5.0) -> None:
    """Background loop: OCR any screenshots with ocr_done = 0.

    `get_conn` is a callable returning a sqlite3 connection owned by this thread
    (sqlite connections are not safe to share across threads).
    """
    if not available():
        return  # nothing to do without a backend
    conn = get_conn()
    while not stop_event.is_set():
        rows = conn.execute(
            "SELECT id, path FROM screenshots WHERE ocr_done = 0 AND deleted = 0 "
            "ORDER BY id LIMIT 10"
        ).fetchall()
        if not rows:
            stop_event.wait(poll)
            continue
        for row in rows:
            text = ocr_image(row["path"])
            conn.execute(
                "UPDATE screenshots SET ocr_text = ?, ocr_done = 1 WHERE id = ?",
                (text, row["id"]),
            )
        conn.commit()
        time.sleep(0.05)

# PyInstaller spec for the daylog Windows build.
# Build:  uv run pyinstaller packaging/daylog.spec --noconfirm
#
# Produces dist/daylog/ (onedir): a windowed daylog.exe + its runtime, which the
# Inno Setup script (packaging/daylog.iss) wraps into the installer.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # project root (SPECPATH = the packaging/ dir)

datas = [
    (str(ROOT / "config.example.toml"), "."),
    (str(ROOT / "README.md"), "."),
]

# pystray / keyboard select a backend at runtime; pull their submodules in.
# winsdk backs the built-in OCR and uiautomation backs the browser-URL lookup — both are
# imported lazily, so PyInstaller cannot see them without help. comtypes is pulled in by its
# own standard hook; only the generated UI Automation client needs naming (and comtypes.test
# must stay out, or it adds ~7 MB of test suite to the installer).
hiddenimports = (
    collect_submodules("pystray")
    + collect_submodules("keyboard")
    + collect_submodules("winsdk")
    + ["uiautomation", "comtypes.gen.UIAutomationClient", "PIL._tkinter_finder"]
)

a = Analysis(
    [str(ROOT / "packaging" / "daylog_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # OCR is the OS engine (winsdk) and the watcher is built in, so the heavy optional
    # backends stay out. The Ollama/Anthropic SDKs are only needed for those providers;
    # LM Studio and OpenAI-compatible servers work over plain HTTP.
    excludes=["rapidocr_onnxruntime", "onnxruntime", "ollama", "anthropic", "tkinter",
              "comtypes.test", "pytest", "unittest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="daylog",
    console=False,                 # windowed: no cmd/console window ever appears
    icon=str(ROOT / "packaging" / "daylog.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="daylog",
)

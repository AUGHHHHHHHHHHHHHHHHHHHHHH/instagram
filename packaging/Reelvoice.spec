# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the macOS desktop app. Run from the repo root:  pyinstaller packaging/Reelvoice.spec
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
from reelvoice import __version__  # noqa: E402

ffbin = ROOT / "build" / "ffbin"
binaries = [(str(ffbin / name), ".") for name in ("ffmpeg", "ffprobe") if (ffbin / name).is_file()]
datas = [
    (str(SRC / "reelvoice" / "ui.html"), "reelvoice"),
    (str(SRC / "reelvoice" / "fonts"), "reelvoice/fonts"),
]
icon = ROOT / "build" / "icon.icns"

a = Analysis(
    [str(SRC / "reelvoice" / "app.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "reelvoice.cli", "reelvoice.server", "reelvoice.video", "reelvoice.captions",
        "webview", "webview.platforms.cocoa", "objc", "AppKit", "Foundation", "WebKit", "Quartz", "Security",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "qtpy", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Reelvoice",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon) if icon.is_file() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Reelvoice")

app = BUNDLE(
    coll,
    name="Reelvoice.app",
    icon=str(icon) if icon.is_file() else None,
    bundle_identifier="app.reelvoice.desktop",
    version=__version__,
    info_plist={
        "CFBundleName": "Reelvoice",
        "CFBundleDisplayName": "Reelvoice",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.video",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSHumanReadableCopyright": "Reelvoice. Voices by Microsoft Edge TTS, video by FFmpeg.",
    },
)

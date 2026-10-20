"""Small shared helpers: ffmpeg lookup (including binaries bundled in the app), probing, paths."""
from __future__ import annotations

import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

APP_NAME = "Reelvoice"


def log(msg: str) -> None:
    """Print a status line to stderr so stdout stays clean for paths/JSON."""
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


def is_mac() -> bool:
    return platform.system() == "Darwin"


def is_frozen() -> bool:
    """True when running from the packaged desktop app (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def app_mode() -> bool:
    """True when the desktop app window is driving us (set by app.py)."""
    return os.environ.get("REELVOICE_APP") == "1"


def bundle_dirs() -> List[Path]:
    """Places where binaries and data live when packaged as an app."""
    dirs: List[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        dirs += [exe_dir, exe_dir.parent / "Frameworks", exe_dir.parent / "Resources"]
    return dirs


@functools.lru_cache(maxsize=None)
def find_tool(name: str) -> Optional[str]:
    """Find a command: bundled with the app first, then PATH, then the usual Homebrew locations.

    A .app launched from Finder does not inherit the shell PATH, so the Homebrew dirs matter.
    """
    for d in bundle_dirs():
        p = d / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    found = shutil.which(name)
    if found:
        return found
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"):
        p = Path(d) / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def ffmpeg() -> Optional[str]:
    return find_tool("ffmpeg")


def ffprobe() -> Optional[str]:
    return find_tool("ffprobe")


def ffmpeg_hint() -> str:
    if is_mac():
        return "Install it with Homebrew:  brew install ffmpeg   (https://brew.sh if you don't have brew)"
    return "Install it with your package manager, e.g.  sudo apt install ffmpeg"


def fonts_dir() -> Optional[Path]:
    """Caption fonts shipped with Reelvoice, so burned-in captions look the same everywhere."""
    here = Path(__file__).resolve().parent / "fonts"
    if here.is_dir() and any(here.glob("*.ttf")):
        return here
    return None


DEFAULT_FONT = "Liberation Sans" if fonts_dir() else "Arial"


def duration_of(path: Path) -> Optional[float]:
    """Length of an audio/video file in seconds. Uses ffprobe, or ffmpeg itself when there is no ffprobe."""
    probe = ffprobe()
    if probe:
        try:
            res = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                capture_output=True, text=True, check=True,
            )
            return float(json.loads(res.stdout)["format"]["duration"])
        except Exception:
            pass
    ff = ffmpeg()
    if ff:
        try:
            res = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
            m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", res.stderr)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
    return None


def slugify(text: str, max_words: int = 5, max_len: int = 40) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)[:max_words]
    slug = "-".join(w.lower() for w in words) or "voice"
    return slug[:max_len].rstrip("-")


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def default_out_dir() -> Path:
    """Where files go: $REELVOICE_OUTPUT, else ~/Movies/Reelvoice in the app, else ./output."""
    env = os.environ.get("REELVOICE_OUTPUT")
    if env:
        return Path(env).expanduser()
    if app_mode() or is_frozen():
        return Path.home() / ("Movies" if is_mac() else "Videos") / APP_NAME
    return Path.cwd() / "output"


def log_file() -> Path:
    if is_mac():
        return Path.home() / "Library" / "Logs" / f"{APP_NAME}.log"
    return Path.home() / ".cache" / APP_NAME.lower() / "app.log"


def unique_path(path: Path) -> Path:
    """Return path, or path-2, path-3 ... if it already exists."""
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def open_file(path: Path) -> None:
    """Open a file or folder with the default app (Finder/QuickTime on Mac)."""
    try:
        if is_mac():
            subprocess.Popen(["open", str(path)])
        elif platform.system() == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        log(f"Could not open {path}: {exc}")


def reveal(path: Path) -> None:
    """Show a file in Finder (or open its folder elsewhere)."""
    if is_mac():
        try:
            subprocess.Popen(["open", "-R", str(path)])
            return
        except Exception as exc:
            log(f"Could not reveal {path}: {exc}")
    open_file(path.parent if path.is_file() else path)


def human_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}" if m else f"{seconds:.1f}s"

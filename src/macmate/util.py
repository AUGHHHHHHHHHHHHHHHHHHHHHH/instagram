"""Small shared helpers: ffmpeg lookup, media probing, slugs, opening files."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def log(msg: str) -> None:
    """Print a status line to stderr so stdout stays clean for paths/JSON."""
    print(msg, file=sys.stderr, flush=True)


def is_mac() -> bool:
    return platform.system() == "Darwin"


def ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def ffprobe() -> Optional[str]:
    return shutil.which("ffprobe")


def ffmpeg_hint() -> str:
    if is_mac():
        return "Install it with Homebrew:  brew install ffmpeg   (https://brew.sh if you don't have brew)"
    return "Install it with your package manager, e.g.  sudo apt install ffmpeg"


def duration_of(path: Path) -> Optional[float]:
    """Length of an audio/video file in seconds, or None if ffprobe is unavailable."""
    probe = ffprobe()
    if not probe:
        return None
    try:
        res = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(res.stdout)["format"]["duration"])
    except Exception:
        return None


def slugify(text: str, max_words: int = 5, max_len: int = 40) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)[:max_words]
    slug = "-".join(w.lower() for w in words) or "voice"
    return slug[:max_len].rstrip("-")


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def default_out_dir() -> Path:
    return Path(os.environ.get("MACMATE_OUTPUT", Path.cwd() / "output"))


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
    """Open a file with the default app (Finder/QuickTime on Mac)."""
    try:
        if is_mac():
            subprocess.Popen(["open", str(path)])
        elif platform.system() == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        log(f"Could not open {path}: {exc}")


def human_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}" if m else f"{seconds:.1f}s"

"""Render a 9:16 Instagram reel with ffmpeg: background + voiceover + burned-in captions (+ music)."""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from . import util
from .engines import EngineError, Speech

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".3gp"}


def classify_background(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in {".heic", ".heif"}:
        raise EngineError("HEIC photos are not supported by ffmpeg. Export the photo as JPG or PNG first.")
    raise EngineError(f"Unsupported background file type '{ext}'. Use a JPG/PNG image or an MP4/MOV video.")


def _escape_filter_path(path: Path) -> str:
    s = str(path)
    for ch in ("\\", ":", "'", "[", "]", ",", ";"):
        s = s.replace(ch, "\\" + ch)
    return s


def _color_for_ffmpeg(color: str) -> str:
    from .captions import parse_color
    r, g, b = parse_color(color)
    return f"0x{r:02X}{g:02X}{b:02X}"


def render_reel(
    speech: Speech,
    out: Path,
    background: Optional[Path] = None,
    bg_color: str = "#111111",
    music: Optional[Path] = None,
    music_volume: float = 0.12,
    ass_text: Optional[str] = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    zoom: bool = True,
    lead: float = 0.3,
    tail: float = 0.7,
    normalize: bool = True,
    log: Callable[[str], None] = util.log,
) -> Path:
    ff = util.ffmpeg()
    if not ff:
        raise EngineError(f"ffmpeg is needed to make videos. {util.ffmpeg_hint()}")
    out = Path(out)
    if out.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        out = out.with_suffix(".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)

    kind = classify_background(background)
    total = speech.duration + lead + tail
    frames = max(1, math.ceil(total * fps))

    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y"]
    if kind == "video":
        cmd += ["-stream_loop", "-1", "-i", str(background)]
    elif kind == "image":
        if not zoom:
            cmd += ["-loop", "1", "-framerate", str(fps)]
        cmd += ["-i", str(background)]
    else:
        cmd += ["-f", "lavfi", "-i", f"color=c={_color_for_ffmpeg(bg_color)}:s={width}x{height}:r={fps}"]
    cmd += ["-i", str(speech.audio)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]

    tmpdir = Path(tempfile.mkdtemp(prefix="macmate-"))
    try:
        # --- video chain
        v = "[0:v]"
        if kind == "image" and zoom:
            bw, bh = width * 2, height * 2
            step = 0.12 / frames
            v += (f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},setsar=1,"
                  f"zoompan=z='min(zoom+{step:.7f},1.15)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":s={width}x{height}:fps={fps},")
        else:
            v += f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps},"
        if ass_text:
            ass_path = tmpdir / "captions.ass"
            ass_path.write_text(ass_text, encoding="utf-8")
            v += f"ass={_escape_filter_path(ass_path)},"
        v += "format=yuv420p[v]"

        # --- audio chain
        a = "[1:a]"
        if normalize:
            a += "loudnorm=I=-16:TP=-1.5:LRA=11,"
        a += f"aresample=44100,adelay={int(lead * 1000)}:all=1,apad=pad_dur={tail:.2f}"
        if music:
            fade_start = max(0.0, total - 1.5)
            a += (f"[va];[2:a]aresample=44100,volume={music_volume:.3f},afade=t=in:d=1,"
                  f"afade=t=out:st={fade_start:.2f}:d=1.5[ma];[va][ma]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            a += "[a]"

        cmd += [
            "-filter_complex", v + ";" + a,
            "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(fps), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart", str(out),
        ]
        log(f"[macmate] Rendering {width}x{height} reel ({util.human_time(total)})... this can take a little while")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            err = res.stderr.strip() or "unknown ffmpeg error"
            raise EngineError("ffmpeg could not render the video:\n" + err[-1500:])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return out

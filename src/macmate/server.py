"""A tiny local web app (standard library only) so you can make voiceovers and reels in the browser."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import webbrowser
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, util
from .captions import CaptionStyle, build_cues, to_ass, to_srt
from .engines import EngineError, Speech, speak
from .video import render_reel
from .voices import ALIASES, DEFAULT_VOICE, list_all_voices, resolve_voice

UI_PATH = Path(__file__).with_name("ui.html")
OUT_DIR = util.default_out_dir()
PREVIEW_TEXT = "Hey! This is how I sound. Pick me for your next reel."
_FAVICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
            b'fill="#ff4d8d"/><path d="M18 46V18l14 16 14-16v28" stroke="#fff" stroke-width="7" fill="none" '
            b'stroke-linecap="round" stroke-linejoin="round"/></svg>')
_preview_lock = threading.Lock()


def _truthy(value: Optional[str], default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).lower() in ("1", "true", "on", "yes")


def parse_multipart(content_type: str, body: bytes) -> Tuple[Dict[str, str], Dict[str, Tuple[str, bytes]]]:
    """Parse a browser form upload with the standard library email parser."""
    raw = b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    msg = BytesParser(policy=policy.HTTP).parsebytes(raw)
    fields: Dict[str, str] = {}
    files: Dict[str, Tuple[str, bytes]] = {}
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename:
            if data:
                files[name] = (filename, data)
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


def _save_upload(item: Optional[Tuple[str, bytes]], folder: Path) -> Optional[Path]:
    if not item:
        return None
    filename, data = item
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name) or "upload"
    target = folder / safe
    target.write_bytes(data)
    return target


def voices_payload() -> dict:
    return {
        "default": DEFAULT_VOICE,
        "aliases": [v.to_dict() for v in ALIASES.values()],
        "all": list_all_voices(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "Macmate/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet unless debugging
        if os.environ.get("MACMATE_DEBUG"):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------ responses
    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"))

    def _error(self, msg, status: int = 400) -> None:
        self._json({"error": str(msg)}, status)

    # ------------------------------------------------------------ routing
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                return self._send(200, UI_PATH.read_bytes(), "text/html; charset=utf-8")
            if path == "/favicon.ico":
                return self._send(200, _FAVICON, "image/svg+xml")
            if path == "/api/status":
                return self._json({"ffmpeg": bool(util.ffmpeg()), "mac": util.is_mac(),
                                   "output_dir": str(OUT_DIR), "version": __version__})
            if path == "/api/voices":
                return self._json(voices_payload())
            if path == "/api/preview":
                return self._preview(query.get("voice", [DEFAULT_VOICE])[0])
            if path.startswith("/files/"):
                return self._file(unquote(path[len("/files/"):]))
            self._error("Not found", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (EngineError, ValueError) as exc:
            self._error(exc, 400)
        except Exception as exc:  # pragma: no cover - last resort
            self._error(f"Unexpected error: {exc}", 500)

    do_HEAD = do_GET

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            if path == "/api/speak":
                return self._speak(json.loads(body.decode("utf-8") or "{}"))
            if path == "/api/reel":
                return self._reel(body)
            self._error("Not found", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (EngineError, ValueError) as exc:
            self._error(exc, 400)
        except Exception as exc:  # pragma: no cover - last resort
            self._error(f"Unexpected error: {exc}", 500)

    # ------------------------------------------------------------ handlers
    def _speak(self, data: dict) -> None:
        text = (data.get("text") or "").strip()
        if not text:
            raise ValueError("Type or paste a script first.")
        words_per_line = int(data.get("words") or 3)
        caps = _truthy(str(data.get("caps", "")))
        out = util.unique_path(OUT_DIR / f"{util.slugify(text)}-{util.timestamp()}.mp3")
        notes: list = []
        speech = speak(text, voice=data.get("voice") or DEFAULT_VOICE, speed=float(data.get("speed") or 1.0),
                       pitch=int(data.get("pitch") or 0), engine=data.get("engine") or "auto", out=out,
                       log=notes.append)
        speech.save_sidecar()
        cues = build_cues(speech.words, words_per_line)
        srt = speech.audio.with_suffix(".srt")
        srt.write_text(to_srt(cues, caps), encoding="utf-8")
        self._json({
            "audio_url": f"/files/{speech.audio.name}", "audio_name": speech.audio.name,
            "srt_url": f"/files/{srt.name}", "duration": speech.duration, "engine": speech.engine,
            "voice": speech.voice, "estimated_timings": speech.estimated_timings, "notes": notes,
            "cues": [{"start": c.start, "end": c.end, "text": c.text} for c in cues],
        })

    def _reel(self, body: bytes) -> None:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            raise ValueError("Expected a form upload.")
        fields, files = parse_multipart(ctype, body)
        text = fields.get("text", "").strip()
        if not text:
            raise ValueError("Type or paste a script first.")
        if not util.ffmpeg():
            raise EngineError(f"ffmpeg is needed to make videos. {util.ffmpeg_hint()}")

        tmpdir = Path(tempfile.mkdtemp(prefix="macmate-upload-"))
        notes: list = []
        try:
            bg = _save_upload(files.get("bg"), tmpdir)
            music = _save_upload(files.get("music"), tmpdir)

            speech = None
            reuse = fields.get("audio_name", "")
            if reuse:
                sidecar = (OUT_DIR / Path(reuse).name).with_suffix(".words.json")
                if sidecar.is_file():
                    try:
                        speech = Speech.from_dict(json.loads(sidecar.read_text()))
                    except Exception:
                        speech = None
                    if speech is not None and not speech.audio.is_file():
                        speech = None
            if speech is None:
                out = util.unique_path(OUT_DIR / f"{util.slugify(text)}-{util.timestamp()}.mp3")
                speech = speak(text, voice=fields.get("voice") or DEFAULT_VOICE,
                               speed=float(fields.get("speed") or 1.0), pitch=int(fields.get("pitch") or 0),
                               engine=fields.get("engine") or "auto", out=out, log=notes.append)
                speech.save_sidecar()

            style = CaptionStyle(
                font=fields.get("font") or "Arial", size=int(fields.get("font_size") or 72),
                color=fields.get("color") or "white", highlight=fields.get("highlight") or "yellow",
                position=fields.get("position") or "bottom", caps=_truthy(fields.get("caps")),
                words_per_line=int(fields.get("words") or 3),
            )
            cues = build_cues(speech.words, style.words_per_line)
            srt = speech.audio.with_suffix(".srt")
            srt.write_text(to_srt(cues, style.caps), encoding="utf-8")
            ass = to_ass(cues, style) if cues and _truthy(fields.get("captions"), True) else None

            video_out = util.unique_path(speech.audio.with_suffix(".mp4"))
            final = render_reel(
                speech, video_out, background=bg, bg_color=fields.get("bg_color") or "#111111",
                music=music, music_volume=float(fields.get("music_volume") or 0.12), ass_text=ass,
                zoom=_truthy(fields.get("zoom"), True), normalize=_truthy(fields.get("normalize"), True),
                log=notes.append,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self._json({
            "video_url": f"/files/{final.name}", "video_name": final.name, "video_path": str(final),
            "audio_url": f"/files/{speech.audio.name}", "audio_name": speech.audio.name,
            "srt_url": f"/files/{srt.name}", "duration": speech.duration, "engine": speech.engine,
            "voice": speech.voice, "notes": notes,
        })

    def _preview(self, voice: str) -> None:
        info = resolve_voice(voice)
        folder = OUT_DIR / "previews"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{info.edge}.mp3"
        with _preview_lock:
            if not target.is_file():
                speak(PREVIEW_TEXT, voice=info.edge, out=target, log=lambda m: None)
        self._json({"url": f"/files/previews/{target.name}", "voice": info.edge})

    def _file(self, name: str) -> None:
        if name.startswith("previews/"):
            target = OUT_DIR / "previews" / Path(name[len("previews/"):]).name
        else:
            target = OUT_DIR / Path(name).name
        if not target.is_file():
            return self._error("Not found", 404)
        size = target.stat().st_size
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f'inline; filename="{target.name}"')
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(target, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1 << 16, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def serve(port: int = 8765, open_browser: Optional[bool] = None) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        util.log(f"Could not start on port {port} ({exc}). Try another port:  macmate --ui {port + 1}")
        return 1
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    print(f"Macmate is running at {url}")
    print(f"Files are saved in {OUT_DIR}")
    print("Press Ctrl+C to stop.")
    if open_browser is None:
        open_browser = not os.environ.get("MACMATE_NO_BROWSER")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0

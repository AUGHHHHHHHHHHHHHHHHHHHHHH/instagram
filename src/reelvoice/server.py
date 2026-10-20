"""The local HTTP backend behind the web page: used by the desktop app window and by `reelvoice --ui`."""
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
from .engines import ENGINE_LABELS, STYLES, EngineError, Speech, elevenlabs_voices, resolve_style, speak
from .settings import api_key, save_settings, settings_path
from .video import render_reel
from .voices import ALIASES, DEFAULT_VOICE, OPENAI_VOICES, list_all_voices, resolve_voice

UI_PATH = Path(__file__).with_name("ui.html")
_preview_lock = threading.Lock()
_FAVICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
            b'fill="#ff4d8d"/><path d="M18 46V18l14 16 14-16v28" stroke="#fff" stroke-width="7" fill="none" '
            b'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def out_dir() -> Path:
    d = util.default_out_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def _output_file(name: str) -> Optional[Path]:
    """Resolve a file name from the API back to a file inside the output folder (no traversal)."""
    base = out_dir()
    if name.startswith("previews/"):
        target = base / "previews" / Path(name[len("previews/"):]).name
    else:
        target = base / Path(name).name
    return target if target.is_file() else None


def status_payload() -> dict:
    return {
        "version": __version__,
        "ffmpeg": bool(util.ffmpeg()),
        "mac": util.is_mac(),
        "app": util.app_mode(),
        "output_dir": str(out_dir()),
        "default_font": util.DEFAULT_FONT,
        "default_voice": DEFAULT_VOICE,
        "styles": [{"name": s.name, "label": s.label, "description": s.description, "voice": s.voice,
                    "openai_voice": s.openai_voice} for s in STYLES.values()],
        "engines": [{"name": n, "label": l} for n, l in ENGINE_LABELS.items()],
        "keys": {"openai": bool(api_key("openai")), "elevenlabs": bool(api_key("elevenlabs"))},
    }


def settings_payload() -> dict:
    return {"openai": bool(api_key("openai")), "elevenlabs": bool(api_key("elevenlabs")), "path": str(settings_path())}


def voices_payload(engine: str = "edge") -> dict:
    if engine == "openai":
        return {"engine": engine, "default": "coral", "all": [], "aliases": [
            {"name": v["name"], "display": v["name"].capitalize(), "gender": v["gender"], "locale": "",
             "language": "OpenAI voices", "note": v["note"]} for v in OPENAI_VOICES]}
    if engine == "elevenlabs":
        voices = elevenlabs_voices()
        return {"engine": engine, "default": voices[0]["id"] if voices else "", "all": [], "aliases": [
            {"name": v["id"], "display": v["name"], "gender": v["gender"] or "?", "locale": "",
             "language": "ElevenLabs · " + (v["category"] or "voices"), "note": v["note"]} for v in voices]}
    return {
        "engine": engine,
        "default": DEFAULT_VOICE,
        "aliases": [v.to_dict() for v in ALIASES.values()],
        "all": list_all_voices(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "Reelvoice/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet unless debugging
        if os.environ.get("REELVOICE_DEBUG"):
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
        q = lambda key, default="": query.get(key, [default])[0]
        try:
            if path in ("/", "/index.html"):
                return self._send(200, UI_PATH.read_bytes(), "text/html; charset=utf-8")
            if path == "/favicon.ico":
                return self._send(200, _FAVICON, "image/svg+xml")
            if path == "/api/status":
                return self._json(status_payload())
            if path == "/api/voices":
                return self._json(voices_payload(q("engine", "edge")))
            if path == "/api/settings":
                return self._json(settings_payload())
            if path == "/api/preview":
                return self._preview(q("voice", ""), q("style", "normal"), q("engine", "auto"))
            if path == "/api/reveal":
                target = _output_file(unquote(q("name")))
                if not target:
                    return self._error("File not found", 404)
                util.reveal(target)
                return self._json({"ok": True})
            if path == "/api/open-output":
                util.open_file(out_dir())
                return self._json({"ok": True, "output_dir": str(out_dir())})
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
            if path == "/api/settings":
                data = json.loads(body.decode("utf-8") or "{}")
                updates = {k: data[k] for k in ("openai_api_key", "elevenlabs_api_key", "elevenlabs_model",
                                                 "elevenlabs_voice", "openai_tts_model") if k in data}
                save_settings(updates)
                return self._json(settings_payload())
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
        out = util.unique_path(out_dir() / f"{util.slugify(text)}-{util.timestamp()}.mp3")
        notes: list = []
        speech = speak(text, voice=data.get("voice") or None, speed=float(data.get("speed") or 1.0),
                       pitch=int(data.get("pitch") or 0), engine=data.get("engine") or "auto", out=out,
                       log=notes.append, style=data.get("style") or "normal")
        speech.save_sidecar()
        cues = build_cues(speech.words, words_per_line)
        srt = speech.audio.with_suffix(".srt")
        srt.write_text(to_srt(cues, caps), encoding="utf-8")
        self._json({
            "audio_url": f"/files/{speech.audio.name}", "audio_name": speech.audio.name,
            "audio_path": str(speech.audio), "srt_url": f"/files/{srt.name}", "srt_name": srt.name,
            "duration": speech.duration, "engine": speech.engine, "voice": speech.voice, "style": speech.style,
            "estimated_timings": speech.estimated_timings, "notes": notes,
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

        tmpdir = Path(tempfile.mkdtemp(prefix="reelvoice-upload-"))
        notes: list = []
        try:
            bg = _save_upload(files.get("bg"), tmpdir)
            music = _save_upload(files.get("music"), tmpdir)

            speech = None
            reuse = fields.get("audio_name", "")
            if reuse:
                sidecar = (out_dir() / Path(reuse).name).with_suffix(".words.json")
                if sidecar.is_file():
                    try:
                        speech = Speech.from_dict(json.loads(sidecar.read_text()))
                    except Exception:
                        speech = None
                    if speech is not None and not speech.audio.is_file():
                        speech = None
            if speech is None:
                out = util.unique_path(out_dir() / f"{util.slugify(text)}-{util.timestamp()}.mp3")
                speech = speak(text, voice=fields.get("voice") or None,
                               speed=float(fields.get("speed") or 1.0), pitch=int(fields.get("pitch") or 0),
                               engine=fields.get("engine") or "auto", out=out, log=notes.append,
                               style=fields.get("style") or "normal")
                speech.save_sidecar()

            style = CaptionStyle(
                font=fields.get("font") or util.DEFAULT_FONT, size=int(fields.get("font_size") or 72),
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
            "srt_url": f"/files/{srt.name}", "srt_name": srt.name, "duration": speech.duration,
            "engine": speech.engine, "voice": speech.voice, "style": speech.style, "notes": notes,
        })

    def _preview(self, voice: str, style_name: str, engine: str) -> None:
        preset = resolve_style(style_name)
        if engine in ("openai", "elevenlabs"):
            label = voice or (preset.openai_voice if engine == "openai" else "default")
        else:
            engine = "auto"
            label = voice = resolve_voice(voice or preset.voice or None).edge
        folder = out_dir() / "previews"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{engine}-{label}-{preset.name}")
        target = folder / f"{safe}.mp3"
        with _preview_lock:
            if not target.is_file():
                speak(preset.sample, voice=voice or None, engine=engine, out=target, log=lambda m: None,
                      style=preset.name)
        self._json({"url": f"/files/previews/{target.name}", "voice": label, "style": preset.name, "engine": engine})

    def _file(self, name: str) -> None:
        target = _output_file(name)
        if not target:
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


def start_server(port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Bind the backend (port 0 = any free port) and serve it on a background thread."""
    out_dir()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="reelvoice-http", daemon=True)
    thread.start()
    return httpd


def serve(port: int = 8765, open_browser: Optional[bool] = None) -> int:
    """`reelvoice --ui`: run the backend in the foreground and open the page in the default browser."""
    try:
        httpd = start_server(port)
    except OSError as exc:
        util.log(f"Could not start on port {port} ({exc}). Try another port:  reelvoice --ui {port + 1}")
        return 1
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"Reelvoice is running at {url}")
    print(f"Files are saved in {out_dir()}")
    print("Press Ctrl+C to stop.")
    if open_browser is None:
        open_browser = not os.environ.get("REELVOICE_NO_BROWSER")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0

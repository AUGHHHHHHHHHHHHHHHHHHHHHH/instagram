"""Text-to-speech engines.

edge  - Microsoft Edge neural voices. AI quality, free, no account. Needs internet. Gives word timings.
gtts  - Google Translate voices. Free, needs internet. Word timings are estimated.
say   - macOS built-in voices. Works offline. Word timings are estimated.

speak() picks the best one that works (edge -> gtts -> say) unless you ask for a specific engine.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import util
from .voices import VoiceInfo, mac_voice_for, resolve_voice

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".aiff"}
ENGINE_NAMES = ("auto", "edge", "gtts", "say")


class EngineError(RuntimeError):
    """Something went wrong talking to a TTS engine (the message is user-friendly)."""


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Speech:
    audio: Path
    words: List[Word] = field(default_factory=list)
    duration: float = 0.0
    engine: str = ""
    voice: str = ""
    estimated_timings: bool = False

    def to_dict(self) -> dict:
        return {
            "audio": str(self.audio), "duration": self.duration, "engine": self.engine, "voice": self.voice,
            "estimated_timings": self.estimated_timings,
            "words": [[w.text, round(w.start, 3), round(w.end, 3)] for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Speech":
        return cls(Path(d["audio"]), [Word(t, s, e) for t, s, e in d["words"]], d["duration"],
                   d.get("engine", ""), d.get("voice", ""), d.get("estimated_timings", False))

    def save_sidecar(self) -> Path:
        path = self.audio.with_suffix(".words.json")
        path.write_text(json.dumps(self.to_dict()))
        return path


# ---------------------------------------------------------------- text clean-up

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002B00-\U00002BFF️‍]+"
)


def clean_text(text: str) -> str:
    """Make pasted scripts speakable: drop markdown symbols and emoji, keep paragraph pauses."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    heading = re.compile(r"^\s*#+\s*")
    bullet = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        lines = []
        for line in block.split("\n"):
            is_heading = bool(heading.match(line)) or bool(bullet.match(line))
            line = bullet.sub("", heading.sub("", line))
            line = re.sub(r"[*_`>#~]+", "", line)
            line = _EMOJI.sub(" ", line)
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            if is_heading and line[-1] not in ".!?:;,":
                line += "."          # a heading or bullet reads as its own sentence
            lines.append(line)
        if not lines:
            continue
        para = " ".join(lines)
        if para[-1] not in ".!?:;,\"'\u201d\u2019)":
            para += "."              # end of a paragraph is a pause
        paragraphs.append(para)
    return " ".join(paragraphs).strip()


# ---------------------------------------------------------------- public entry point

def speak(
    text: str,
    voice: str = "ava",
    speed: float = 1.0,
    pitch: int = 0,
    engine: str = "auto",
    out: Optional[Path] = None,
    log: Callable[[str], None] = util.log,
) -> Speech:
    """Synthesize `text` to an audio file and return the Speech (path, word timings, duration)."""
    text = clean_text(text)
    if not text:
        raise EngineError("There is no text to speak.")
    if engine not in ENGINE_NAMES:
        raise EngineError(f"Unknown engine '{engine}'. Choose one of: {', '.join(ENGINE_NAMES)}")
    info = resolve_voice(voice)
    speed = max(0.5, min(2.0, float(speed)))

    if out is None:
        out = util.default_out_dir() / f"{util.slugify(text)}-{util.timestamp()}.mp3"
    out = Path(out)
    if out.suffix.lower() not in AUDIO_EXTS:
        out = out.with_suffix(".mp3")
    out.parent.mkdir(parents=True, exist_ok=True)

    order = ["edge", "gtts", "say"] if engine == "auto" else [engine]
    errors = []
    for name in order:
        if name == "say" and not util.is_mac():
            if engine == "say":
                raise EngineError("The 'say' engine is the built-in macOS speech and only exists on a Mac.")
            continue
        try:
            return _ENGINES[name](text, info, speed, pitch, out, log)
        except EngineError as exc:
            errors.append(f"{name}: {exc}")
            if engine != "auto":
                raise
            log(f"[macmate] {name} did not work ({exc}). Trying the next engine...")
    raise EngineError("No text-to-speech engine worked:\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------- edge (AI neural voices)

def _edge(text: str, info: VoiceInfo, speed: float, pitch: int, out: Path, log) -> Speech:
    try:
        import edge_tts
    except ImportError as exc:
        raise EngineError("edge-tts is not installed (pip install edge-tts)") from exc

    rate = f"{int(round((speed - 1) * 100)):+d}%"
    pitch_s = f"{int(pitch):+d}Hz"
    tmp = out if out.suffix.lower() == ".mp3" else out.with_name(out.stem + ".edge-tmp.mp3")

    async def run() -> List[Word]:
        comm = edge_tts.Communicate(text, info.edge, rate=rate, pitch=pitch_s, boundary="WordBoundary")
        words: List[Word] = []
        with open(tmp, "wb") as fh:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append(Word(chunk["text"], start, start + chunk["duration"] / 1e7))
        return words

    try:
        words = asyncio.run(asyncio.wait_for(run(), timeout=180))
    except Exception as exc:  # network / SSL / service errors
        if tmp.exists():
            tmp.unlink()
        raise EngineError(_friendly_network_error(exc)) from exc
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise EngineError("the voice service sent no audio (try again, or a different voice)")

    words = _reattach_punctuation(words, text)
    audio = _finalize_format(tmp, out, log)
    duration = util.duration_of(audio) or (words[-1].end + 0.3 if words else _mp3_duration_guess(audio, 48))
    return Speech(audio, words, duration, "edge", info.edge)


def _reattach_punctuation(words: List[Word], text: str) -> List[Word]:
    """Edge reports bare words; copy punctuation back from the original text so captions read well."""
    tokens = text.split()
    norm = lambda s: re.sub(r"[^\w]+", "", s, flags=re.U).lower()
    pos = 0
    fixed: List[Word] = []
    for w in words:
        target = norm(w.text)
        hit = None
        for j in range(pos, min(pos + 4, len(tokens))):
            if target and norm(tokens[j]) == target:
                hit = j
                break
        if hit is not None:
            fixed.append(Word(tokens[hit].strip('"“”‘’\'()[]'), w.start, w.end))
            pos = hit + 1
        else:
            fixed.append(w)
    return fixed


def _friendly_network_error(exc: Exception) -> str:
    s = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in s:
        return ("SSL certificate problem. On a Mac with python.org Python, run "
                "'Install Certificates.command' from your Python folder in Applications")
    if "403" in s or "Forbidden" in s:
        return "the Edge voice service refused the request (wait a minute and try again)"
    if any(k in s for k in ("Cannot connect", "getaddrinfo", "Timeout", "timed out", "Network is unreachable")):
        return "no internet connection (edge voices need internet)"
    if "NoAudioReceived" in type(exc).__name__ or "No audio" in s:
        return "no audio received (unsupported characters or empty text?)"
    return s.splitlines()[0][:200] if s else type(exc).__name__


# ---------------------------------------------------------------- gtts (Google Translate voices)

def _gtts(text: str, info: VoiceInfo, speed: float, pitch: int, out: Path, log) -> Speech:
    try:
        from gtts import gTTS
    except ImportError as exc:
        raise EngineError("gTTS is not installed (pip install gTTS)") from exc
    lang, tld = info.gtts
    tmp = out.with_name(out.stem + ".gtts-tmp.mp3")
    try:
        gTTS(text=text, lang=lang, tld=tld).save(str(tmp))
    except Exception as exc:
        raise EngineError(_friendly_network_error(exc)) from exc

    if abs(speed - 1.0) > 0.01:
        if util.ffmpeg():
            sped = out.with_name(out.stem + ".gtts-speed.mp3")
            _run_ffmpeg(["-i", str(tmp), "-filter:a", f"atempo={speed:.3f}", "-c:a", "libmp3lame", "-q:a", "4", str(sped)])
            tmp.unlink()
            tmp = sped
        else:
            log("[macmate] speed needs ffmpeg; using normal speed for the gtts engine")

    audio = _finalize_format(tmp, out, log)
    duration = util.duration_of(audio) or _mp3_duration_guess(audio, 32)
    return Speech(audio, estimate_words(text, duration), duration, "gtts", f"google-{lang}-{tld}", True)


# ---------------------------------------------------------------- say (macOS offline)

def _say(text: str, info: VoiceInfo, speed: float, pitch: int, out: Path, log) -> Speech:
    voice = mac_voice_for(info)
    with tempfile.TemporaryDirectory(prefix="macmate-say-") as td:
        txt = Path(td) / "script.txt"
        aiff = Path(td) / "voice.aiff"
        txt.write_text(text, encoding="utf-8")
        cmd = ["say", "-r", str(int(round(175 * speed))), "-o", str(aiff), "-f", str(txt)]
        if voice:
            cmd[1:1] = ["-v", voice]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise EngineError("the 'say' command was not found (is this a Mac?)") from exc
        except subprocess.CalledProcessError as exc:
            raise EngineError((exc.stderr or exc.stdout or "say failed").strip()) from exc

        if util.ffmpeg():
            audio = _finalize_format(aiff, out, log, keep_source=True)
        else:
            audio = out.with_suffix(".m4a")
            try:
                subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(audio)],
                               capture_output=True, text=True, check=True)
            except Exception as exc:
                raise EngineError(f"could not convert audio: {exc}") from exc
            if audio != out:
                log(f"[macmate] ffmpeg not found, saved as .m4a instead: {audio}")
    duration = util.duration_of(audio) or _estimate_duration(text, speed)
    return Speech(audio, estimate_words(text, duration), duration, "say", voice or "system default", True)


_ENGINES = {"edge": _edge, "gtts": _gtts, "say": _say}


# ---------------------------------------------------------------- helpers

def _run_ffmpeg(args: List[str]) -> None:
    ff = util.ffmpeg()
    if not ff:
        raise EngineError(f"ffmpeg is needed for this step. {util.ffmpeg_hint()}")
    res = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise EngineError("ffmpeg failed: " + (res.stderr.strip().splitlines() or ["unknown error"])[-1])


def _finalize_format(src: Path, out: Path, log, keep_source: bool = False) -> Path:
    """Convert src to the format `out` asks for (by extension). Falls back to mp3 if ffmpeg is missing."""
    if src == out:
        return out
    if src.suffix.lower() == out.suffix.lower():
        src.replace(out)
        return out
    if util.ffmpeg():
        codec = {".wav": ["-c:a", "pcm_s16le"], ".m4a": ["-c:a", "aac", "-b:a", "160k"],
                 ".aac": ["-c:a", "aac", "-b:a", "160k"], ".mp3": ["-c:a", "libmp3lame", "-q:a", "3"]}
        _run_ffmpeg(["-i", str(src), *codec.get(out.suffix.lower(), []), str(out)])
        if not keep_source:
            src.unlink()
        return out
    fallback = out.with_suffix(".mp3") if src.suffix.lower() == ".mp3" else out.with_suffix(src.suffix)
    src.replace(fallback)
    log(f"[macmate] ffmpeg not found, so the audio was saved as {fallback.suffix}: {fallback}")
    return fallback


def _mp3_duration_guess(path: Path, kbps: int) -> float:
    try:
        return path.stat().st_size * 8 / (kbps * 1000)
    except OSError:
        return 0.0


def _estimate_duration(text: str, speed: float) -> float:
    return max(0.5, len(text.split()) / (2.75 * speed))


def estimate_words(text: str, duration: float) -> List[Word]:
    """Spread the words over `duration` seconds, weighting by length and punctuation pauses."""
    tokens = text.split()
    if not tokens or duration <= 0:
        return []
    weights = []
    for tok in tokens:
        w = len(re.sub(r"[^\w]", "", tok, flags=re.U)) + 1.0
        if tok[-1] in ".!?":
            w += 3.0
        elif tok[-1] in ",;:":
            w += 1.5
        weights.append(w)
    total = sum(weights)
    words, t = [], 0.0
    for tok, w in zip(tokens, weights):
        d = duration * w / total
        words.append(Word(tok, round(t, 3), round(t + d * 0.9, 3)))
        t += d
    return words

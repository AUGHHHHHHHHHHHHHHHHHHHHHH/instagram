"""Text-to-speech engines.

edge        - Microsoft Edge neural voices. Free, no account. Needs internet. Gives word timings.
gtts        - Google Translate voices. Free, needs internet. Word timings are estimated.
say         - macOS built-in voices. Works offline. Word timings are estimated.
openai      - OpenAI gpt-4o-mini-tts. Expressive: the style's tone description steers the delivery. Needs an API key.
elevenlabs  - ElevenLabs. The most natural voices, with per-style stability/expressiveness. Needs an API key.

speak() picks the best one that works (edge -> gtts -> say) unless you ask for a specific engine.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import util
from .settings import api_key, setting
from .voices import OPENAI_VOICES, VoiceInfo, mac_voice_for, resolve_voice

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".aiff"}
ENGINE_NAMES = ("auto", "edge", "gtts", "say", "openai", "elevenlabs")
PAID_ENGINES = {"openai": "OpenAI", "elevenlabs": "ElevenLabs"}
ENGINE_LABELS = {
    "auto": "Auto (free voices)",
    "edge": "AI neural voices (free, needs internet)",
    "gtts": "Google Translate voice (free, needs internet)",
    "say": "Mac built-in voice (works offline)",
    "openai": "OpenAI (expressive, tone from a description, needs key)",
    "elevenlabs": "ElevenLabs (most natural, needs key)",
}


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
    style: str = "normal"
    loudness: float = -16.0     # LUFS target used when rendering a reel

    def to_dict(self) -> dict:
        return {
            "audio": str(self.audio), "duration": self.duration, "engine": self.engine, "voice": self.voice,
            "estimated_timings": self.estimated_timings, "style": self.style, "loudness": self.loudness,
            "words": [[w.text, round(w.start, 3), round(w.end, 3)] for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Speech":
        return cls(Path(d["audio"]), [Word(t, s, e) for t, s, e in d["words"]], d["duration"],
                   d.get("engine", ""), d.get("voice", ""), d.get("estimated_timings", False),
                   d.get("style", "normal"), d.get("loudness", -16.0))

    def save_sidecar(self) -> Path:
        path = self.audio.with_suffix(".words.json")
        path.write_text(json.dumps(self.to_dict()))
        return path


# ---------------------------------------------------------------- voice styles

@dataclass(frozen=True)
class Style:
    name: str
    label: str
    description: str
    speed: float = 1.0          # multiplied with the user's speed
    pitch: int = 0              # added to the user's pitch (Hz, AI voices only)
    filter: str = ""            # ffmpeg audio filter chain applied after synthesis
    loudness: float = -16.0     # LUFS target when a reel is rendered
    voice: str = ""             # suggested Edge voice when the user did not pick one
    sample: str = "Hey! This is how I sound. Pick me for your next reel."
    instructions: str = ""      # tone description for engines that take one (OpenAI)
    tag: str = ""               # ElevenLabs v3 audio tag put in front of the text, e.g. "[whispers]"
    eleven: Tuple[float, float, float] = (0.5, 0.75, 0.0)   # stability, similarity, style exaggeration
    filter_expressive: str = "" # light polish for expressive engines (no fake whisper layer)
    openai_voice: str = ""      # suggested OpenAI voice


_WHISPER = ("afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*2*PI)'"
            ":imag='hypot(re,im)*sin((random(1)*2-1)*2*PI)':win_size=128:overlap=0.8")
# slow left/right drift, like an ASMR artist moving from ear to ear
_DRIFT = "aformat=channel_layouts=stereo,apulsator=mode=sine:amount=0.3:hz=0.05"

STYLES = {
    "normal": Style("normal", "Normal", "Natural delivery", openai_voice="coral"),
    "asmr": Style(
        "asmr", "Soft & soothing (ASMR)",
        "Mommy-ASMR: slow, soft-spoken, breathy, warm and close to the mic, drifting ear to ear.",
        speed=0.8, pitch=-5, loudness=-21.0, voice="jenny", openai_voice="shimmer",
        instructions=("Mommy ASMR. Speak extremely softly and gently, barely above a whisper, very slowly, "
                      "warm, loving and reassuring, like a caring mother soothing someone right next to the "
                      "microphone. Breathy, tender, affectionate intonation with small pauses between phrases. "
                      "Never loud, never energetic, never announcer-like."),
        tag="[whispers]", eleven=(0.3, 0.8, 0.7),
        filter_expressive=("highpass=f=60,equalizer=f=220:t=q:w=1.2:g=2,"
                           "acompressor=threshold=-24dB:ratio=2:attack=30:release=350:makeup=1,"
                           f"{_DRIFT},volume=0.9"),
        filter=(
            "[0:a]highpass=f=70,lowpass=f=10000,asplit=2[dry][wet];"
            f"[wet]{_WHISPER},lowpass=f=6500,volume=0.5[breath];"
            "[dry]volume=0.75[voice];"
            "[voice][breath]amix=inputs=2:normalize=0,"
            "equalizer=f=220:t=q:w=1.2:g=3,equalizer=f=3500:t=q:w=1.5:g=-3,equalizer=f=6500:t=q:w=2:g=-3,"
            "acompressor=threshold=-26dB:ratio=2.5:attack=40:release=400:makeup=2,"
            f"{_DRIFT},volume=0.8[out]"
        ),
        sample="Hey, sweetheart. You did so well today. Come here. Just relax, and let me take care of you.",
    ),
    "whisper": Style(
        "whisper", "Whisper (ASMR)",
        "A full whisper, close to the mic. Pair with short sentences.",
        speed=0.82, pitch=-3, loudness=-22.0, voice="jenny", openai_voice="shimmer",
        instructions=("Whisper every single word, very quietly and intimately, slowly, like an ASMR video "
                      "recorded close to the microphone. Soft breathy consonants, gentle and caring."),
        tag="[whispers]", eleven=(0.3, 0.8, 0.6),
        filter_expressive=("highpass=f=70,equalizer=f=250:t=q:w=1.2:g=2,"
                           "acompressor=threshold=-26dB:ratio=2.5:attack=20:release=300:makeup=2,"
                           f"{_DRIFT},volume=0.9"),
        filter=(
            f"[0:a]highpass=f=90,{_WHISPER},lowpass=f=7500,"
            "equalizer=f=260:t=q:w=1.2:g=3,"
            "acompressor=threshold=-28dB:ratio=3:attack=20:release=300:makeup=5,"
            f"{_DRIFT},volume=0.9[out]"
        ),
        sample="Shh. It's okay. You're safe here. Close your eyes, and breathe with me.",
    ),
    "calm": Style(
        "calm", "Calm storyteller", "Relaxed pace, slightly lower, smooth.",
        speed=0.9, pitch=-3, loudness=-17.0, openai_voice="sage",
        instructions="Calm, relaxed storyteller. Slow, smooth, slightly lower, soothing and unhurried.",
        eleven=(0.6, 0.75, 0.2), filter_expressive="acompressor=threshold=-20dB:ratio=2:attack=20:release=250",
        filter="highpass=f=60,equalizer=f=200:t=q:w=1:g=2,acompressor=threshold=-20dB:ratio=2:attack=20:release=250",
        sample="Once upon a time, in a quiet little town, something unexpected happened.",
    ),
    "energetic": Style(
        "energetic", "Energetic", "Faster and brighter, for hooks and hype.",
        speed=1.12, pitch=4, loudness=-15.0, openai_voice="nova",
        instructions="Upbeat, energetic, bright and fast, like an excited creator hooking viewers in the first second.",
        eleven=(0.4, 0.75, 0.6), filter_expressive="acompressor=threshold=-18dB:ratio=3:attack=10:release=150",
        filter="equalizer=f=4000:t=q:w=1.5:g=2,acompressor=threshold=-18dB:ratio=3:attack=10:release=150",
        sample="Stop scrolling! You need to hear this right now.",
    ),
    "deep": Style(
        "deep", "Deep & cinematic", "Lower, slower, movie-trailer feel.",
        speed=0.95, pitch=-14, loudness=-16.0, voice="christopher", openai_voice="onyx",
        instructions="Deep, slow, cinematic movie-trailer narrator. Gravitas, long pauses, low and resonant.",
        eleven=(0.6, 0.8, 0.3), filter_expressive="equalizer=f=110:t=q:w=1:g=3,acompressor=threshold=-20dB:ratio=2.5:attack=20:release=300",
        filter="equalizer=f=110:t=q:w=1:g=4,equalizer=f=2500:t=q:w=1.5:g=-1.5,acompressor=threshold=-20dB:ratio=2.5:attack=20:release=300",
        sample="In a world where every second counts, one voice rises above the noise.",
    ),
}


def resolve_style(name: Optional[str]) -> Style:
    key = (name or "normal").strip().lower().replace("-", "").replace("_", "")
    aliases = {"mommy": "asmr", "mommyasmr": "asmr", "soft": "asmr", "soothing": "asmr", "whispering": "whisper",
               "story": "calm", "hype": "energetic", "trailer": "deep", "cinematic": "deep"}
    key = aliases.get(key, key)
    if key not in STYLES:
        raise EngineError(f"Unknown style '{name}'. Choose one of: {', '.join(STYLES)}")
    return STYLES[key]


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

@dataclass
class SpeakRequest:
    text: str
    voice: Optional[str]            # raw voice name/id (used by the expressive engines)
    info: Optional[VoiceInfo]       # resolved Edge voice (used by edge / gtts / say)
    speed: float
    pitch: int
    out: Path
    log: Callable[[str], None]
    preset: Style


def speak(
    text: str,
    voice: Optional[str] = "ava",
    speed: float = 1.0,
    pitch: int = 0,
    engine: str = "auto",
    out: Optional[Path] = None,
    log: Callable[[str], None] = util.log,
    style: str = "normal",
) -> Speech:
    """Synthesize `text` to an audio file and return the Speech (path, word timings, duration)."""
    text = clean_text(text)
    if not text:
        raise EngineError("There is no text to speak.")
    if engine not in ENGINE_NAMES:
        raise EngineError(f"Unknown engine '{engine}'. Choose one of: {', '.join(ENGINE_NAMES)}")
    preset = resolve_style(style)
    voice = (voice or "").strip() or None
    if engine in PAID_ENGINES:
        if not api_key(engine):
            raise EngineError(f"{PAID_ENGINES[engine]} needs an API key. Add it under Settings in the app, "
                              f"or run:  reelvoice --set-key {engine} YOUR_KEY")
        info = None
    else:
        info = resolve_voice(voice or preset.voice or None)
    speed = max(0.5, min(2.0, float(speed) * preset.speed))
    pitch = int(pitch) + preset.pitch

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
            req = SpeakRequest(text, voice, info, speed, pitch, out, log, preset)
            speech = _ENGINES[name](req)
            speech.style, speech.loudness = preset.name, preset.loudness
            audio_filter = preset.filter_expressive if name in PAID_ENGINES else preset.filter
            if audio_filter:
                _apply_filter(speech, audio_filter, log)
            return speech
        except EngineError as exc:
            errors.append(f"{name}: {exc}")
            if engine != "auto":
                raise
            log(f"[reelvoice] {name} did not work ({exc}). Trying the next engine...")
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
            log("[reelvoice] speed needs ffmpeg; using normal speed for the gtts engine")

    audio = _finalize_format(tmp, out, log)
    duration = util.duration_of(audio) or _mp3_duration_guess(audio, 32)
    return Speech(audio, estimate_words(text, duration), duration, "gtts", f"google-{lang}-{tld}", True)


# ---------------------------------------------------------------- say (macOS offline)

def _say(text: str, info: VoiceInfo, speed: float, pitch: int, out: Path, log) -> Speech:
    voice = mac_voice_for(info)
    with tempfile.TemporaryDirectory(prefix="reelvoice-say-") as td:
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
                log(f"[reelvoice] ffmpeg not found, saved as .m4a instead: {audio}")
    duration = util.duration_of(audio) or _estimate_duration(text, speed)
    return Speech(audio, estimate_words(text, duration), duration, "say", voice or "system default", True)


# ---------------------------------------------------------------- OpenAI (expressive, instruction-steered)

def _http(method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: int = 240) -> Tuple[int, bytes]:
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        raise EngineError(_friendly_network_error(exc)) from exc


def _api_error(service: str, status: int, data: bytes) -> str:
    detail = ""
    try:
        payload = json.loads(data.decode("utf-8", "replace"))
        err = payload.get("error") or payload.get("detail") or payload
        if isinstance(err, dict):
            detail = err.get("message") or json.dumps(err)[:200]
        else:
            detail = str(err)[:200]
    except Exception:
        detail = data[:200].decode("utf-8", "replace")
    if status == 401:
        return f"{service} rejected the API key. Check it under Settings."
    if status in (402, 429):
        return f"{service} says you are out of credits or rate limited ({detail})"
    return f"{service} error {status}: {detail}"


def _openai(req: SpeakRequest) -> Speech:
    key = api_key("openai")
    model = setting("openai_tts_model", "gpt-4o-mini-tts")
    voice = (req.voice or req.preset.openai_voice or "coral").lower()
    names = [v["name"] for v in OPENAI_VOICES]
    if voice not in names:
        raise EngineError(f"Unknown OpenAI voice '{voice}'. Options: {', '.join(names)}")
    instructions = req.preset.instructions
    pace = ("Speak very slowly." if req.speed < 0.85 else "Speak slowly." if req.speed < 0.95
            else "Speak quickly." if req.speed > 1.1 else "")
    if pace:
        instructions = f"{instructions} {pace}".strip()
    body: dict = {"model": model, "input": req.text, "voice": voice, "response_format": "mp3"}
    if model.startswith("gpt-4o"):
        if instructions:
            body["instructions"] = instructions
    elif abs(req.speed - 1.0) > 0.02:
        body["speed"] = round(req.speed, 2)
    status, data = _http("POST", "https://api.openai.com/v1/audio/speech",
                         {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         json.dumps(body).encode("utf-8"))
    if status != 200:
        raise EngineError(_api_error("OpenAI", status, data))
    tmp = req.out.with_name(req.out.stem + ".openai-tmp.mp3")
    tmp.write_bytes(data)
    audio = _finalize_format(tmp, req.out, req.log)
    duration = util.duration_of(audio) or _estimate_duration(req.text, req.speed)
    return Speech(audio, estimate_words(req.text, duration), duration, "openai", f"openai/{voice}", True)


# ---------------------------------------------------------------- ElevenLabs

_eleven_cache: Dict[str, Tuple[float, List[dict]]] = {}


def elevenlabs_voices(key: Optional[str] = None) -> List[dict]:
    """Voices in the user's ElevenLabs library: [{id, name, gender, category, note}]."""
    key = key or api_key("elevenlabs")
    if not key:
        raise EngineError("ElevenLabs needs an API key. Add it under Settings in the app.")
    cached = _eleven_cache.get(key)
    if cached and time.time() - cached[0] < 600:
        return cached[1]
    status, data = _http("GET", "https://api.elevenlabs.io/v1/voices", {"xi-api-key": key}, None, timeout=60)
    if status != 200:
        raise EngineError(_api_error("ElevenLabs", status, data))
    voices = []
    for v in json.loads(data).get("voices", []):
        labels = v.get("labels") or {}
        note = ", ".join(x for x in (labels.get("description"), labels.get("accent"), labels.get("use_case")) if x)
        voices.append({"id": v["voice_id"], "name": v.get("name", v["voice_id"]),
                       "gender": (labels.get("gender") or "")[:1].upper(), "category": v.get("category", ""),
                       "note": note})
    _eleven_cache[key] = (time.time(), voices)
    return voices


def _eleven_voice(name_or_id: str, key: str) -> Tuple[str, str]:
    """Resolve a voice name (e.g. 'Rachel') or voice_id to (voice_id, display name)."""
    wanted = name_or_id.strip()
    voices = elevenlabs_voices(key)
    for v in voices:
        if v["id"] == wanted:
            return v["id"], v["name"]
    for v in voices:
        if v["name"].lower() == wanted.lower():
            return v["id"], v["name"]
    if re.fullmatch(r"[A-Za-z0-9]{15,40}", wanted):
        return wanted, wanted
    names = ", ".join(v["name"] for v in voices[:25])
    raise EngineError(f"ElevenLabs voice '{wanted}' not found in your library. Available: {names}")


def _words_from_alignment(alignment: Optional[dict]) -> Optional[List[Word]]:
    if not alignment:
        return None
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: List[Word] = []
    cur, start, end = "", 0.0, 0.0
    for ch, cs, ce in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append(Word(cur, start, end))
                cur = ""
        else:
            if not cur:
                start = cs
            cur += ch
            end = ce
    if cur:
        words.append(Word(cur, start, end))
    words = [w for w in words if not (w.text.startswith("[") and w.text.endswith("]"))]   # audio tags
    return words or None


def _elevenlabs(req: SpeakRequest) -> Speech:
    key = api_key("elevenlabs")
    model = setting("elevenlabs_model", "eleven_multilingual_v2")
    voice_id, label = _eleven_voice(req.voice or setting("elevenlabs_voice") or "Rachel", key)
    stability, similarity, exaggeration = req.preset.eleven
    voice_settings: dict = {"stability": stability, "similarity_boost": similarity, "style": exaggeration,
                            "use_speaker_boost": True}
    if abs(req.speed - 1.0) > 0.02:
        voice_settings["speed"] = round(max(0.7, min(1.2, req.speed)), 2)
    text = req.text
    if req.preset.tag and model.startswith("eleven_v3"):
        text = f"{req.preset.tag} {text}"
    body = json.dumps({"text": text, "model_id": model, "voice_settings": voice_settings}).encode("utf-8")
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    base = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    words: Optional[List[Word]] = None
    status, data = _http("POST", f"{base}/with-timestamps?output_format=mp3_44100_128",
                         {**headers, "Accept": "application/json"}, body)
    if status == 200:
        payload = json.loads(data)
        audio_bytes = base64.b64decode(payload["audio_base64"])
        words = _words_from_alignment(payload.get("alignment") or payload.get("normalized_alignment"))
    else:
        if status == 401 or status in (402, 429):
            raise EngineError(_api_error("ElevenLabs", status, data))
        req.log(f"[reelvoice] ElevenLabs timestamps unavailable ({status}); using estimated caption timing")
        status2, data2 = _http("POST", f"{base}?output_format=mp3_44100_128", {**headers, "Accept": "audio/mpeg"}, body)
        if status2 != 200:
            raise EngineError(_api_error("ElevenLabs", status2, data2))
        audio_bytes = data2

    tmp = req.out.with_name(req.out.stem + ".eleven-tmp.mp3")
    tmp.write_bytes(audio_bytes)
    audio = _finalize_format(tmp, req.out, req.log)
    duration = util.duration_of(audio) or (words[-1].end + 0.3 if words else _estimate_duration(req.text, req.speed))
    estimated = words is None
    if estimated:
        words = estimate_words(req.text, duration)
    return Speech(audio, words, duration, "elevenlabs", f"elevenlabs/{label}", estimated)


_ENGINES = {
    "edge": lambda r: _edge(r.text, r.info, r.speed, r.pitch, r.out, r.log),
    "gtts": lambda r: _gtts(r.text, r.info, r.speed, r.pitch, r.out, r.log),
    "say": lambda r: _say(r.text, r.info, r.speed, r.pitch, r.out, r.log),
    "openai": _openai,
    "elevenlabs": _elevenlabs,
}


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
    log(f"[reelvoice] ffmpeg not found, so the audio was saved as {fallback.suffix}: {fallback}")
    return fallback


def _apply_filter(speech: Speech, audio_filter: str, log) -> None:
    """Run the finished audio through an ffmpeg filter chain (voice styles). Timing is unchanged."""
    if not util.ffmpeg():
        log("[reelvoice] ffmpeg not found, so the voice style's audio processing was skipped")
        return
    src = speech.audio
    tmp = src.with_name(src.stem + ".style-tmp" + src.suffix)
    codec = {".wav": ["-c:a", "pcm_s16le"], ".m4a": ["-c:a", "aac", "-b:a", "160k"],
             ".aac": ["-c:a", "aac", "-b:a", "160k"]}.get(src.suffix.lower(), ["-c:a", "libmp3lame", "-q:a", "2"])
    graph = audio_filter if audio_filter.startswith("[") else f"[0:a]{audio_filter}[out]"
    _run_ffmpeg(["-i", str(src), "-filter_complex", graph, "-map", "[out]", *codec, str(tmp)])
    tmp.replace(src)
    speech.duration = util.duration_of(src) or speech.duration


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

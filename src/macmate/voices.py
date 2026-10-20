"""Voice catalogue: friendly names like "ava" or "brian", lookup, and the full voice list."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import util

DEFAULT_VOICE = "ava"


@dataclass(frozen=True)
class VoiceInfo:
    alias: str      # friendly short name, "" when the user gave a full Edge name
    edge: str       # full Microsoft Edge voice name, e.g. en-US-AvaMultilingualNeural
    gender: str     # "F", "M" or ""
    locale: str     # e.g. en-US
    note: str = ""

    @property
    def lang(self) -> str:
        return self.locale.split("-")[0].lower()

    @property
    def name(self) -> str:
        return self.alias or self.edge

    @property
    def gtts(self) -> Tuple[str, str]:
        """(language code, top-level domain) for Google TTS, chosen to match the accent."""
        return _GTTS_BY_LOCALE.get(self.locale, (self.lang, "com"))

    def to_dict(self) -> dict:
        return {"name": self.name, "alias": self.alias, "edge": self.edge, "gender": self.gender,
                "locale": self.locale, "language": language_name(self.locale), "note": self.note}


def _v(alias: str, edge: str, gender: str, note: str = "") -> VoiceInfo:
    locale = "-".join(edge.split("-")[:2])
    return VoiceInfo(alias, edge, gender, locale, note)


# Friendly names. The first entry is the default. English first, then other languages.
_ALIAS_LIST: List[VoiceInfo] = [
    _v("ava", "en-US-AvaMultilingualNeural", "F", "Expressive, friendly. Great all-rounder for reels"),
    _v("andrew", "en-US-AndrewMultilingualNeural", "M", "Warm, confident. Great all-rounder for reels"),
    _v("emma", "en-US-EmmaMultilingualNeural", "F", "Cheerful, clear"),
    _v("brian", "en-US-BrianMultilingualNeural", "M", "Casual, approachable"),
    _v("aria", "en-US-AriaNeural", "F", "Positive, confident newsreader"),
    _v("jenny", "en-US-JennyNeural", "F", "Friendly, considerate"),
    _v("michelle", "en-US-MichelleNeural", "F", "Pleasant, soft"),
    _v("ana", "en-US-AnaNeural", "F", "Cute, cartoon-like kid voice"),
    _v("guy", "en-US-GuyNeural", "M", "Energetic, passionate"),
    _v("christopher", "en-US-ChristopherNeural", "M", "Deep, authoritative"),
    _v("eric", "en-US-EricNeural", "M", "Calm, rational"),
    _v("roger", "en-US-RogerNeural", "M", "Lively"),
    _v("steffan", "en-US-SteffanNeural", "M", "Measured, rational"),
    _v("sonia", "en-GB-SoniaNeural", "F", "British, friendly"),
    _v("libby", "en-GB-LibbyNeural", "F", "British, bright"),
    _v("ryan", "en-GB-RyanNeural", "M", "British, friendly"),
    _v("thomas", "en-GB-ThomasNeural", "M", "British, calm"),
    _v("natasha", "en-AU-NatashaNeural", "F", "Australian, friendly"),
    _v("william", "en-AU-WilliamMultilingualNeural", "M", "Australian, friendly"),
    _v("emily", "en-IE-EmilyNeural", "F", "Irish"),
    _v("neerja", "en-IN-NeerjaNeural", "F", "Indian English"),
    _v("clara", "en-CA-ClaraNeural", "F", "Canadian"),
    _v("elvira", "es-ES-ElviraNeural", "F", "Spanish (Spain)"),
    _v("alvaro", "es-ES-AlvaroNeural", "M", "Spanish (Spain)"),
    _v("dalia", "es-MX-DaliaNeural", "F", "Spanish (Mexico)"),
    _v("jorge", "es-MX-JorgeNeural", "M", "Spanish (Mexico)"),
    _v("denise", "fr-FR-DeniseNeural", "F", "French"),
    _v("henri", "fr-FR-HenriNeural", "M", "French"),
    _v("katja", "de-DE-KatjaNeural", "F", "German"),
    _v("conrad", "de-DE-ConradNeural", "M", "German"),
    _v("francisca", "pt-BR-FranciscaNeural", "F", "Portuguese (Brazil)"),
    _v("antonio", "pt-BR-AntonioNeural", "M", "Portuguese (Brazil)"),
    _v("elsa", "it-IT-ElsaNeural", "F", "Italian"),
    _v("diego", "it-IT-DiegoNeural", "M", "Italian"),
    _v("nanami", "ja-JP-NanamiNeural", "F", "Japanese"),
    _v("keita", "ja-JP-KeitaNeural", "M", "Japanese"),
    _v("sunhi", "ko-KR-SunHiNeural", "F", "Korean"),
    _v("injoon", "ko-KR-InJoonNeural", "M", "Korean"),
    _v("xiaoxiao", "zh-CN-XiaoxiaoNeural", "F", "Chinese (Mandarin)"),
    _v("yunxi", "zh-CN-YunxiNeural", "M", "Chinese (Mandarin)"),
    _v("swara", "hi-IN-SwaraNeural", "F", "Hindi"),
    _v("madhur", "hi-IN-MadhurNeural", "M", "Hindi"),
    _v("zariyah", "ar-SA-ZariyahNeural", "F", "Arabic"),
    _v("svetlana", "ru-RU-SvetlanaNeural", "F", "Russian"),
    _v("colette", "nl-NL-ColetteNeural", "F", "Dutch"),
    _v("emel", "tr-TR-EmelNeural", "F", "Turkish"),
    _v("hoaimy", "vi-VN-HoaiMyNeural", "F", "Vietnamese"),
    _v("gadis", "id-ID-GadisNeural", "F", "Indonesian"),
]
ALIASES: Dict[str, VoiceInfo] = {v.alias: v for v in _ALIAS_LIST}

_GTTS_BY_LOCALE: Dict[str, Tuple[str, str]] = {
    "en-US": ("en", "com"), "en-GB": ("en", "co.uk"), "en-AU": ("en", "com.au"), "en-IE": ("en", "ie"),
    "en-IN": ("en", "co.in"), "en-CA": ("en", "ca"), "en-ZA": ("en", "co.za"), "en-NZ": ("en", "co.nz"),
    "es-ES": ("es", "es"), "es-MX": ("es", "com.mx"), "es-US": ("es", "us"),
    "fr-FR": ("fr", "fr"), "fr-CA": ("fr", "ca"), "de-DE": ("de", "de"), "pt-BR": ("pt", "com.br"),
    "pt-PT": ("pt", "pt"), "it-IT": ("it", "it"), "ja-JP": ("ja", "com"), "ko-KR": ("ko", "com"),
    "zh-CN": ("zh-CN", "com"), "zh-TW": ("zh-TW", "com"), "hi-IN": ("hi", "co.in"), "ar-SA": ("ar", "com"),
    "ru-RU": ("ru", "com"), "nl-NL": ("nl", "nl"), "tr-TR": ("tr", "com"), "vi-VN": ("vi", "com"),
    "id-ID": ("id", "co.id"),
}

_LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "hi": "Hindi", "ar": "Arabic", "ru": "Russian",
    "nl": "Dutch", "tr": "Turkish", "vi": "Vietnamese", "id": "Indonesian", "pl": "Polish", "sv": "Swedish",
    "da": "Danish", "nb": "Norwegian", "fi": "Finnish", "el": "Greek", "he": "Hebrew", "th": "Thai",
    "uk": "Ukrainian", "cs": "Czech", "hu": "Hungarian", "ro": "Romanian", "ms": "Malay", "fil": "Filipino",
    "ta": "Tamil", "te": "Telugu", "bn": "Bengali", "ur": "Urdu", "fa": "Persian", "sw": "Swahili",
}
_REGION_NAMES = {
    "US": "US", "GB": "UK", "AU": "Australia", "IE": "Ireland", "IN": "India", "CA": "Canada", "ZA": "South Africa",
    "NZ": "New Zealand", "ES": "Spain", "MX": "Mexico", "FR": "France", "DE": "Germany", "BR": "Brazil",
    "PT": "Portugal", "IT": "Italy", "JP": "Japan", "KR": "Korea", "CN": "China", "TW": "Taiwan", "HK": "Hong Kong",
    "SA": "Saudi Arabia", "RU": "Russia", "NL": "Netherlands", "TR": "Turkey", "VN": "Vietnam", "ID": "Indonesia",
}


def language_name(locale: str) -> str:
    parts = locale.split("-")
    lang = _LANGUAGE_NAMES.get(parts[0].lower(), parts[0])
    region = _REGION_NAMES.get(parts[-1].upper(), parts[-1]) if len(parts) > 1 else ""
    return f"{lang} ({region})" if region else lang


# ---------------------------------------------------------------- full Edge voice list (cached)

_CACHE = Path.home() / ".cache" / "macmate" / "voices.json"
_CACHE_TTL = 7 * 24 * 3600


def list_all_voices(refresh: bool = False, allow_network: bool = True) -> List[dict]:
    """Every Edge voice as {name, gender, locale, language, personalities}. Cached for a week."""
    if not refresh and _CACHE.exists() and time.time() - _CACHE.stat().st_mtime < _CACHE_TTL:
        try:
            return json.loads(_CACHE.read_text())
        except Exception:
            pass
    if not allow_network:
        return _cached_voices_any_age()
    try:
        import edge_tts
        raw = asyncio.run(edge_tts.list_voices())
    except Exception:
        return _cached_voices_any_age()
    voices = []
    for item in raw:
        tag = item.get("VoiceTag", {}) or {}
        voices.append({
            "name": item["ShortName"],
            "gender": (item.get("Gender") or "")[:1].upper(),
            "locale": item.get("Locale", ""),
            "language": language_name(item.get("Locale", "")),
            "personalities": ", ".join(tag.get("VoicePersonalities", []) or []),
        })
    voices.sort(key=lambda v: (not v["locale"].startswith("en"), v["locale"], v["name"]))
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(voices))
    except Exception:
        pass
    return voices


def _cached_voices_any_age() -> List[dict]:
    try:
        return json.loads(_CACHE.read_text())
    except Exception:
        return []


# ---------------------------------------------------------------- lookup

def resolve_voice(name: Optional[str]) -> VoiceInfo:
    """Turn 'ava', 'Brian', 'en-GB-SoniaNeural' or a fuzzy 'sonia' into a VoiceInfo."""
    raw = (name or DEFAULT_VOICE).strip()
    key = raw.lower()
    if key in ALIASES:
        return ALIASES[key]

    # Exact full Edge name (any letter case) from the cached list, else as typed.
    cached = _cached_voices_any_age()
    by_lower = {v["name"].lower(): v for v in cached}
    if key in by_lower:
        v = by_lower[key]
        return VoiceInfo("", v["name"], v["gender"], v["locale"])
    if re.match(r"^[a-z]{2,3}-[a-z]{2,4}-[a-z]+neural$", key):
        for alias in ALIASES.values():
            if alias.edge.lower() == key:
                return alias
        parts = raw.split("-")
        locale = f"{parts[0].lower()}-{parts[1].upper()}"
        edge = raw if any(c.isupper() for c in raw) else f"{locale}-{parts[2][:-6].capitalize()}Neural"
        return VoiceInfo("", edge, "", locale)

    # Fuzzy: substring of an alias or of a full name.
    for alias, info in ALIASES.items():
        if key in alias or key in info.edge.lower():
            return info
    for v in cached:
        if key in v["name"].lower():
            return VoiceInfo("", v["name"], v["gender"], v["locale"])
    raise ValueError(f"Unknown voice '{raw}'. Run  macmate --voices  to see the options.")


# ---------------------------------------------------------------- macOS "say" voices

_MAC_PREFERRED = {
    ("en-US", "F"): ["Samantha", "Ava", "Allison", "Susan", "Zoe", "Nicky", "Flo", "Sandy", "Shelley"],
    ("en-US", "M"): ["Alex", "Tom", "Evan", "Nathan", "Reed", "Eddy", "Rocko", "Fred"],
    ("en-GB", "F"): ["Kate", "Serena", "Stephanie", "Fiona"],
    ("en-GB", "M"): ["Daniel", "Oliver", "Arthur"],
    ("en-AU", "F"): ["Karen", "Catherine"],
    ("en-AU", "M"): ["Lee", "Gordon"],
    ("en-IE", "F"): ["Moira"],
    ("en-IN", "F"): ["Veena", "Isha"],
    ("en-IN", "M"): ["Rishi"],
    ("es-ES", "F"): ["Mónica", "Monica", "Marisol"],
    ("es-ES", "M"): ["Jorge"],
    ("es-MX", "F"): ["Paulina", "Angelica"],
    ("es-MX", "M"): ["Juan"],
    ("fr-FR", "F"): ["Amélie", "Amelie", "Audrey", "Aurélie"],
    ("fr-FR", "M"): ["Thomas", "Nicolas"],
    ("de-DE", "F"): ["Anna", "Petra"],
    ("de-DE", "M"): ["Markus", "Yannick"],
    ("it-IT", "F"): ["Alice", "Federica"],
    ("it-IT", "M"): ["Luca"],
    ("pt-BR", "F"): ["Luciana", "Joana"],
    ("pt-BR", "M"): ["Felipe"],
    ("ja-JP", "F"): ["Kyoko", "O-ren"],
    ("ja-JP", "M"): ["Otoya", "Hattori"],
    ("ko-KR", "F"): ["Yuna", "Sora"],
    ("zh-CN", "F"): ["Tingting", "Ting-Ting"],
    ("hi-IN", "F"): ["Lekha"],
}
_mac_voices_cache: Optional[List[Tuple[str, str]]] = None


def mac_voices() -> List[Tuple[str, str]]:
    """[(name, locale)] of voices installed on this Mac, e.g. ("Samantha", "en_US")."""
    global _mac_voices_cache
    if _mac_voices_cache is not None:
        return _mac_voices_cache
    voices: List[Tuple[str, str]] = []
    if util.is_mac():
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
            for line in out.splitlines():
                m = re.match(r"^(.*?)\s{2,}([a-zA-Z]{2,3}_[A-Za-z0-9_]+)\s*#", line)
                if m:
                    voices.append((m.group(1).strip(), m.group(2)))
        except Exception:
            pass
    _mac_voices_cache = voices
    return voices


def mac_voice_for(info: VoiceInfo) -> Optional[str]:
    """Best installed macOS voice for the requested voice, or None for the system default."""
    installed = mac_voices()
    if not installed:
        return None
    names = {n for n, _ in installed}
    if info.alias and info.alias.capitalize() in names:
        return info.alias.capitalize()
    for candidate in _MAC_PREFERRED.get((info.locale, info.gender), []):
        if candidate in names:
            return candidate
    for n, loc in installed:
        if loc.replace("_", "-").lower() == info.locale.lower():
            return n
    for n, loc in installed:
        if loc.lower().startswith(info.lang):
            return n
    return None

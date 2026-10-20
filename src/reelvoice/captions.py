"""Turn word timings into captions: SRT for editors and ASS for burned-in reel captions."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .engines import Word

_COLORS = {
    "white": (255, 255, 255), "black": (0, 0, 0), "yellow": (255, 221, 0), "gold": (255, 196, 0),
    "red": (235, 50, 50), "green": (60, 220, 100), "lime": (170, 255, 0), "blue": (60, 140, 255),
    "cyan": (0, 220, 255), "magenta": (255, 60, 220), "pink": (255, 110, 180), "orange": (255, 140, 0),
    "purple": (170, 90, 255),
}


@dataclass
class CaptionStyle:
    font: str = "Arial"
    size: int = 72               # pixels at 1080x1920
    color: str = "white"         # base text color
    highlight: str = "yellow"    # the word being spoken
    outline: str = "black"
    position: str = "bottom"     # top | center | bottom
    caps: bool = False
    words_per_line: int = 3      # 0 = full sentences


@dataclass
class Cue:
    start: float
    end: float
    words: List[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


# ---------------------------------------------------------------- grouping

def build_cues(words: List[Word], words_per_line: int = 3, max_gap: float = 0.6) -> List[Cue]:
    """Group words into short caption chunks, breaking at sentence ends and pauses."""
    if words_per_line <= 0:
        max_words, max_chars = 8, 42
    else:
        max_words, max_chars = words_per_line, max(16, words_per_line * 9)

    cues: List[Cue] = []
    cur: List[Word] = []

    def flush() -> None:
        if cur:
            cues.append(Cue(cur[0].start, cur[-1].end, list(cur)))
            cur.clear()

    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            chars = sum(len(x.text) for x in cur) + len(cur) + len(w.text)
            if (len(cur) >= max_words or chars > max_chars or gap > max_gap
                    or cur[-1].text[-1:] in ".!?"):
                flush()
        cur.append(w)
    flush()

    # Keep each caption up until the next one starts (no flicker), with a short hold at the end.
    for i, cue in enumerate(cues):
        if i + 1 < len(cues):
            nxt = cues[i + 1].start
            cue.end = nxt if nxt - cue.end < 1.0 else cue.end + 0.3
        else:
            cue.end += 0.4
    return cues


# ---------------------------------------------------------------- SRT

def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: List[Cue], caps: bool = False) -> str:
    lines = []
    for i, cue in enumerate(cues, 1):
        text = cue.text.upper() if caps else cue.text
        lines.append(f"{i}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{text}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------- ASS (burned-in, word highlight)

def parse_color(value: str) -> Tuple[int, int, int]:
    v = (value or "white").strip().lower()
    if v in _COLORS:
        return _COLORS[v]
    v = v.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{3}", v):
        v = "".join(c * 2 for c in v)
    if re.fullmatch(r"[0-9a-f]{6}", v):
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    raise ValueError(f"Unknown color '{value}'. Use a name like yellow or a hex code like #ffdd00.")


def _ass_color(value: str, alpha: int = 0) -> str:
    r, g, b = parse_color(value)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def to_ass(cues: List[Cue], style: Optional[CaptionStyle] = None, width: int = 1080, height: int = 1920) -> str:
    """One Dialogue line per spoken word: the whole caption is shown, the current word is highlighted."""
    style = style or CaptionStyle()
    align, margin_v = {"top": (8, int(height * 0.16)), "center": (5, 0)}.get(style.position, (2, int(height * 0.27)))
    outline = max(2, round(style.size / 14))
    hl = _ass_color(style.highlight)[2:]   # without "&H" prefix for inline override

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{style.font},{style.size},{_ass_color(style.color)},{_ass_color(style.color)},{_ass_color(style.outline)},{_ass_color("black", 128)},-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 3)},{align},70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        n = len(cue.words)
        for i, w in enumerate(cue.words):
            start = cue.start if i == 0 else w.start
            end = cue.end if i == n - 1 else cue.words[i + 1].start
            if end - start < 0.01:
                continue
            parts = []
            for j, other in enumerate(cue.words):
                t = _ass_escape(other.text.upper() if style.caps else other.text)
                parts.append(f"{{\\1c&H{hl}&\\fscx108\\fscy108}}{t}{{\\r}}" if j == i else t)
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(events) + "\n"

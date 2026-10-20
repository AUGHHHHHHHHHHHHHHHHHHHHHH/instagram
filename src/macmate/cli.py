"""Command line interface.

    macmate "Your script here"                   -> output/your-script-here-<time>.mp3 + .srt
    macmate script.txt -v brian --speed 1.1
    macmate "text" --video clip.mp4              -> 9:16 reel with voice + animated captions
    macmate "text" --image photo.jpg --music beat.mp3
    macmate --voices                             -> list voices
    macmate --ui                                 -> open the local web app
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import __version__, util
from .captions import CaptionStyle, build_cues, to_ass, to_srt
from .engines import AUDIO_EXTS, ENGINE_NAMES, EngineError, speak
from .video import render_reel
from .voices import ALIASES, DEFAULT_VOICE, language_name, list_all_voices


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="macmate",
        description="Easy AI text-to-speech voiceovers and auto-captioned reels for Instagram.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  macmate "Three tips to grow faster on Instagram."
  macmate script.txt -v andrew --speed 1.1 -o voice.mp3
  macmate script.txt --video broll.mp4 -o reel.mp4
  macmate "text" --image photo.jpg --music beat.mp3 --caps --highlight lime
  macmate --voices          macmate --voices es          macmate --ui
""")
    p.add_argument("text", nargs="?", help="the script to speak, a path to a .txt file, or - for stdin")
    p.add_argument("-o", "--out", help="output file (.mp3/.wav/.m4a for audio, .mp4 for a reel). Default: output/")
    p.add_argument("-v", "--voice", default=DEFAULT_VOICE, help=f"voice name (default: {DEFAULT_VOICE}). See --voices")
    p.add_argument("-s", "--speed", type=float, default=1.0, help="speaking speed, 0.5 to 2.0 (default 1.0)")
    p.add_argument("--pitch", type=int, default=0, help="pitch shift in Hz for AI voices, e.g. -20 or 15")
    p.add_argument("-e", "--engine", choices=ENGINE_NAMES, default="auto",
                   help="auto (default), edge = AI voices, gtts = Google, say = Mac offline")
    p.add_argument("--open", action="store_true", help="open the result when done")

    cap = p.add_argument_group("captions")
    cap.add_argument("--no-captions", action="store_true", help="skip writing the .srt / burned-in captions")
    cap.add_argument("--words", type=int, default=3, help="words per caption (default 3; 0 = whole sentences)")
    cap.add_argument("--caps", action="store_true", help="UPPERCASE captions")
    cap.add_argument("--font", default="Arial", help="caption font name (default Arial)")
    cap.add_argument("--font-size", type=int, default=72, help="caption size in px on a 1080x1920 frame (default 72)")
    cap.add_argument("--color", default="white", help="caption text color (default white)")
    cap.add_argument("--highlight", default="yellow", help="color of the word being spoken (default yellow)")
    cap.add_argument("--position", choices=["top", "center", "bottom"], default="bottom")

    reel = p.add_argument_group("make a reel (needs ffmpeg)")
    reel.add_argument("--video", metavar="FILE", help="background video; it is looped or trimmed to fit")
    reel.add_argument("--image", metavar="FILE", help="background photo (JPG/PNG) with a slow zoom")
    reel.add_argument("--bg", metavar="COLOR", help="solid background color instead of media, e.g. '#101010'")
    reel.add_argument("--music", metavar="FILE", help="background music (audio or video file), looped, faded")
    reel.add_argument("--music-volume", type=float, default=0.12, help="music level 0-1 (default 0.12)")
    reel.add_argument("--no-zoom", action="store_true", help="keep a photo background still")
    reel.add_argument("--no-normalize", action="store_true", help="skip loudness normalization of the voice")

    other = p.add_argument_group("other")
    other.add_argument("--voices", nargs="?", const="all", metavar="LANG",
                       help="list friendly voices, optionally for a language code (en, es, fr...)")
    other.add_argument("--all-voices", action="store_true", help="list every available AI voice (300+)")
    other.add_argument("--ui", nargs="?", const=8765, type=int, metavar="PORT",
                       help="start the local web app (default port 8765) and open it in your browser")
    other.add_argument("--version", action="version", version=f"macmate {__version__}")
    return p


def read_text(arg: Optional[str]) -> str:
    if arg == "-" or (arg is None and not sys.stdin.isatty()):
        return sys.stdin.read()
    if arg is None:
        return ""
    candidate = Path(arg).expanduser()
    if candidate.suffix.lower() in {".txt", ".md"} and candidate.is_file():
        return candidate.read_text(encoding="utf-8", errors="replace")
    if candidate.is_file() and len(arg) < 260:
        return candidate.read_text(encoding="utf-8", errors="replace")
    return arg


def print_voices(lang: str, show_all: bool) -> None:
    if show_all:
        voices = list_all_voices()
        if not voices:
            print("Could not fetch the voice list (no internet?). Here are the built-in names:\n")
            print_voices("all", False)
            return
        print(f"{'VOICE NAME':38} {'G':1}  {'LANGUAGE':22} PERSONALITY")
        for v in voices:
            if lang != "all" and not v["locale"].lower().startswith(lang.lower()):
                continue
            print(f"{v['name']:38} {v['gender']:1}  {v['language']:22} {v['personalities']}")
        print("\nUse any of these with  -v NAME")
        return
    print(f"{'NAME':13} {'G':1}  {'LANGUAGE':22} STYLE")
    for info in ALIASES.values():
        if lang != "all" and not info.locale.lower().startswith(lang.lower()):
            continue
        star = " (default)" if info.alias == DEFAULT_VOICE else ""
        print(f"{info.alias:13} {info.gender:1}  {language_name(info.locale):22} {info.note}{star}")
    print("\nUse with  -v NAME   e.g.  macmate \"Hello\" -v brian      (--all-voices shows 300+ more)")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.voices or args.all_voices:
        print_voices(args.voices or "all", args.all_voices)
        return 0
    if args.ui is not None:
        from .server import serve
        return serve(port=args.ui)

    text = read_text(args.text).strip()
    if not text:
        parser.print_help()
        return 2

    reel_mode = bool(args.video or args.image or args.bg) or (
        args.out and Path(args.out).suffix.lower() in {".mp4", ".mov", ".m4v"})
    for label, value in (("--video", args.video), ("--image", args.image), ("--music", args.music)):
        if value and not Path(value).expanduser().is_file():
            parser.error(f"{label}: file not found: {value}")

    # Decide file names.
    if args.out:
        out = Path(args.out).expanduser()
        if reel_mode:
            video_out = out if out.suffix.lower() in {".mp4", ".mov", ".m4v"} else out.with_suffix(".mp4")
            audio_out = video_out.with_suffix(".mp3")
        else:
            audio_out = out if out.suffix.lower() in AUDIO_EXTS else out.with_suffix(".mp3")
            video_out = None
    else:
        base = util.default_out_dir() / f"{util.slugify(text)}-{util.timestamp()}"
        audio_out = base.with_suffix(".mp3")
        video_out = base.with_suffix(".mp4") if reel_mode else None

    if reel_mode and not util.ffmpeg():
        util.log(f"ffmpeg is needed to make a video. {util.ffmpeg_hint()}")
        return 1

    try:
        speech = speak(text, voice=args.voice, speed=args.speed, pitch=args.pitch, engine=args.engine, out=audio_out)
    except (EngineError, ValueError) as exc:
        util.log(f"Error: {exc}")
        return 1

    style = CaptionStyle(font=args.font, size=args.font_size, color=args.color, highlight=args.highlight,
                         position=args.position, caps=args.caps, words_per_line=args.words)
    srt_path = None
    cues = build_cues(speech.words, args.words) if speech.words else []
    if cues and not args.no_captions:
        srt_path = speech.audio.with_suffix(".srt")
        srt_path.write_text(to_srt(cues, args.caps), encoding="utf-8")

    print(f"Voice:    {speech.voice}  ({speech.engine} engine, {util.human_time(speech.duration)})")
    print(f"Audio:    {speech.audio}")
    if srt_path:
        note = "  (timings estimated)" if speech.estimated_timings else ""
        print(f"Captions: {srt_path}{note}")

    final: Path = speech.audio
    if reel_mode:
        try:
            ass = to_ass(cues, style) if cues and not args.no_captions else None
            bg = Path(args.video or args.image).expanduser() if (args.video or args.image) else None
            final = render_reel(
                speech, video_out, background=bg, bg_color=args.bg or "#111111",
                music=Path(args.music).expanduser() if args.music else None, music_volume=args.music_volume,
                ass_text=ass, zoom=not args.no_zoom, normalize=not args.no_normalize,
            )
        except (EngineError, ValueError) as exc:
            util.log(f"Error: {exc}")
            return 1
        print(f"Reel:     {final}   (1080x1920, ready to upload)")

    if args.open:
        util.open_file(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())

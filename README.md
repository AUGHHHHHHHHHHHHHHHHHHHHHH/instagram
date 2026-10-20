# Macmate

Easy AI text-to-speech for Instagram videos. Type a script, pick a voice, and get a clean
voiceover (MP3) with auto-captions, or a finished 9:16 reel with the captions burned in.

- **AI voices** from Microsoft Edge (free, no account, 300+ voices in 70+ languages)
- **Fallbacks** that just work: Google Translate voice, or your Mac's own offline voice
- **Auto-captions** timed per word, with the spoken word highlighted (CapCut style)
- **One-shot reels**: photo or video background + voice + captions + music → `reel.mp4`
- **Two ways to use it**: a simple web app in your browser, or one-line commands

## Quick start (Mac)

```bash
git clone https://github.com/AUGHHHHHHHHHHHHHHHHHHHHHH/instagram.git
cd instagram
./macmate --ui
```

The first run sets itself up (about 30 seconds), then opens the app in your browser.
Or just double-click **Macmate.command** in Finder.

For making videos you also need ffmpeg (one time):

```bash
brew install ffmpeg
```

Voiceovers work without ffmpeg. Only the video rendering needs it.

## Using the command line

```bash
./macmate "Three tips to grow faster on Instagram."
```

That writes `output/three-tips-to-grow-faster-<time>.mp3` plus a matching `.srt` caption file.

```bash
./macmate script.txt -v andrew --speed 1.1          # read a text file, male voice, a bit faster
./macmate script.txt --video broll.mp4 -o reel.mp4  # full reel: voice + animated captions over your clip
./macmate script.txt --image photo.jpg --music beat.mp3 --caps --highlight lime
./macmate "text" --bg "#101010"                     # reel on a solid background (no media needed)
./macmate --voices                                  # friendly voice names
./macmate --voices es                               # voices for a language
./macmate --all-voices                              # every AI voice (300+)
./macmate --ui                                      # the web app
```

`./macmate --help` lists every option.

### Voices

| Name | | Style |
|---|---|---|
| `ava` (default) | F | Expressive, friendly |
| `andrew` | M | Warm, confident |
| `emma` | F | Cheerful, clear |
| `brian` | M | Casual, approachable |
| `aria`, `jenny`, `michelle`, `ana` | F | US English |
| `guy`, `christopher`, `eric`, `roger`, `steffan` | M | US English |
| `sonia`, `libby`, `ryan`, `thomas` | | British |
| `natasha`, `william` | | Australian |
| `elvira`, `alvaro`, `dalia`, `jorge` | | Spanish |
| `denise`, `henri`, `katja`, `conrad`, `elsa`, `diego`, `francisca`, `antonio` | | French, German, Italian, Portuguese |
| `nanami`, `keita`, `sunhi`, `xiaoxiao`, `yunxi`, `swara`, `madhur` | | Japanese, Korean, Chinese, Hindi |

Any full voice name from `--all-voices` also works, e.g. `-v en-GB-SoniaNeural`.

### Options that matter

| Option | What it does |
|---|---|
| `-v NAME` | voice (see above) |
| `-s 1.1` | speed, 0.5 to 2.0 |
| `-o FILE` | where to save (`.mp3`, `.wav`, `.m4a`, or `.mp4` for a reel) |
| `--video FILE` | background video, looped or trimmed to fit the voice |
| `--image FILE` | background photo with a slow zoom (`--no-zoom` to keep it still) |
| `--bg COLOR` | solid background instead, e.g. `--bg black` or `--bg "#1a1a2e"` |
| `--music FILE` | background music, looped and faded, `--music-volume 0.12` |
| `--words 3` | words per caption (1 to 6, or 0 for whole sentences) |
| `--caps` | UPPERCASE captions |
| `--color`, `--highlight` | caption text color and the color of the word being spoken |
| `--font-size 72`, `--font Arial`, `--position bottom` | caption look |
| `--no-captions` | no captions at all |
| `-e edge|gtts|say` | force an engine (default `auto`: AI voice, then Google, then Mac) |
| `--open` | open the result when done |

## How the engines compare

| Engine | Quality | Needs internet | Word timings for captions |
|---|---|---|---|
| `edge` (default) | AI neural, very natural | yes | exact |
| `gtts` | decent | yes | estimated |
| `say` (Mac) | classic Siri-era voices | no | estimated |

With `auto`, Macmate tries them in that order and tells you if it had to fall back.

## Output

Everything lands in `output/` next to where you ran the command (set `MACMATE_OUTPUT` to change it):

- `name.mp3` the voiceover (24 kHz mono MP3 from the AI voice)
- `name.srt` captions you can drop into CapCut, Premiere, or Instagram's caption tool
- `name.mp4` the reel: 1080×1920, H.264, AAC, 30 fps, loudness-normalized voice

Reels upload straight to Instagram. Keep them under 90 seconds.

## Tips for good voiceovers

- Short sentences. Periods and commas control the pauses.
- Spell out numbers you want read a certain way (`twenty twenty-six` instead of `2026`).
- Markdown and emoji are stripped automatically, so pasting from ChatGPT is fine.
- Preview a voice in the web app before generating a long script.

## Troubleshooting

- **"ffmpeg is needed"**: `brew install ffmpeg` (install [Homebrew](https://brew.sh) first if you don't have it).
- **SSL certificate error** with python.org Python: open Applications → Python 3.x → run *Install Certificates.command*.
- **HEIC photos** from an iPhone aren't supported by ffmpeg. Export as JPG first.
- **No internet**: use `-e say` for the Mac's offline voice.
- **Port in use** for the web app: `./macmate --ui 8766`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m macmate --help
```

The code is small: `src/macmate/`: `engines.py` (voices), `captions.py` (SRT/ASS), `video.py` (ffmpeg), `cli.py`, `server.py` + `ui.html`.

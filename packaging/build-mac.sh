#!/usr/bin/env bash
# Build Reelvoice.app (+ .dmg and .zip) on a Mac. Used by GitHub Actions, works locally too.
set -euo pipefail
cd "$(dirname "$0")/.."

ARCH="$(uname -m)"
VERSION="$(python3 -c 'import sys; sys.path.insert(0, "src"); import reelvoice; print(reelvoice.__version__)')"
echo "==> Building Reelvoice ${VERSION} for macOS ${ARCH}"

echo "==> Python dependencies"
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -e ".[app]" "pyinstaller>=6.6" >/dev/null
python3 -c "import webview, objc, WebKit; print('pywebview ok')"

echo "==> Static ffmpeg + ffprobe"
mkdir -p build/ffbin
if [ ! -x build/ffbin/ffmpeg ] || [ ! -x build/ffbin/ffprobe ]; then
  case "$ARCH" in
    arm64)
      curl -fsSL --retry 3 -o build/ffmpeg.zip  https://www.osxexperts.net/ffmpeg71arm.zip
      curl -fsSL --retry 3 -o build/ffprobe.zip https://www.osxexperts.net/ffprobe71arm.zip
      ;;
    *)
      echo "No static ffmpeg source is configured for ${ARCH}. Build on Apple Silicon." >&2
      exit 1
      ;;
  esac
  unzip -o -q build/ffmpeg.zip  -d build/ffbin
  unzip -o -q build/ffprobe.zip -d build/ffbin
  rm -rf build/ffbin/__MACOSX
fi
chmod +x build/ffbin/ffmpeg build/ffbin/ffprobe
xattr -cr build/ffbin || true
# (no pipes into early-exiting readers here: with pipefail a SIGPIPE would fail the build)
build/ffbin/ffmpeg -version > build/ffmpeg-version.txt
build/ffbin/ffprobe -version > build/ffprobe-version.txt
sed -n 1p build/ffmpeg-version.txt build/ffprobe-version.txt
build/ffbin/ffmpeg -hide_banner -filters > build/ffmpeg-filters.txt
for f in " ass " " afftfilt " " apulsator " " loudnorm " " zoompan "; do
  grep -q "$f" build/ffmpeg-filters.txt || { echo "bundled ffmpeg lacks the$f filter" >&2; exit 1; }
done

echo "==> App icon"
rm -rf build/icon.iconset && mkdir -p build/icon.iconset
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" assets/icon.png --out "build/icon.iconset/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z "$d" "$d" assets/icon.png --out "build/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns build/icon.iconset -o build/icon.icns

echo "==> PyInstaller"
rm -rf dist/Reelvoice dist/Reelvoice.app
pyinstaller --noconfirm --clean packaging/Reelvoice.spec
APP="dist/Reelvoice.app"
BIN="$APP/Contents/MacOS/Reelvoice"
test -x "$BIN"

echo "==> Smoke tests (the app binary doubles as the CLI)"
"$BIN" --version
"$BIN" --styles
"$BIN" "Smoke test for the packaged app. Captions should appear." -e say --style asmr --bg "#202020" --words 2 -o build/smoke.mp4
test -s build/smoke.mp4
echo "smoke reel: $(stat -f%z build/smoke.mp4) bytes"

echo "==> Packaging"
ZIP="dist/Reelvoice-${VERSION}-macOS-${ARCH}.zip"
DMG="dist/Reelvoice-${VERSION}-macOS-${ARCH}.dmg"
rm -f "$ZIP" "$DMG"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
rm -rf build/dmg && mkdir -p build/dmg
cp -R "$APP" build/dmg/
ln -s /Applications build/dmg/Applications
hdiutil create -volname "Reelvoice" -srcfolder build/dmg -ov -format UDZO "$DMG" >/dev/null
ls -la dist/*.dmg dist/*.zip
echo "==> Done"

"""Desktop app entry point: a native window showing the Reelvoice interface.

Run with arguments and it behaves exactly like the `reelvoice` command line, so the
executable inside Reelvoice.app doubles as a CLI.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _setup_logging() -> None:
    """Windowed apps have no terminal; keep a log file so problems can be diagnosed."""
    try:
        path = Path.home() / "Library" / "Logs" / "Reelvoice.log" if sys.platform == "darwin" \
            else Path.home() / ".cache" / "reelvoice" / "app.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 2_000_000:
            path.unlink()
        fh = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = fh
        sys.stderr = fh
    except Exception:
        pass


def run_desktop() -> int:
    os.environ["REELVOICE_APP"] = "1"
    _setup_logging()
    from reelvoice import __version__, util          # absolute imports: PyInstaller runs this file as __main__
    from reelvoice.server import out_dir, start_server

    print(f"Reelvoice {__version__} starting (ffmpeg: {util.ffmpeg()})")
    httpd = start_server(port=0)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Backend on {url}, output folder {out_dir()}")

    try:
        import webview
    except ImportError:
        print("pywebview is not installed; opening the default browser instead")
        import webbrowser
        webbrowser.open(url)
        try:
            import threading
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass
    webview.create_window("Reelvoice", url, width=920, height=1040, min_size=(680, 600), text_select=True)
    webview.start()          # blocks until the window is closed (Cmd+Q / red button)
    print("Window closed, shutting down")
    httpd.shutdown()
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]   # Finder adds -psn_... on older macOS
    if args:
        from reelvoice.cli import main as cli_main
        return cli_main(args)
    return run_desktop()


if __name__ == "__main__":
    sys.exit(main())

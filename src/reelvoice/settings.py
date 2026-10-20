"""Local settings: API keys for the expressive engines. Stored in a user-only file, never sent anywhere else."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

from . import util

ENV_KEYS = {"openai": "OPENAI_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}


def settings_path() -> Path:
    if util.is_mac():
        return Path.home() / "Library" / "Application Support" / util.APP_NAME / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / util.APP_NAME.lower() / "settings.json"


def load_settings() -> Dict[str, str]:
    try:
        data = json.loads(settings_path().read_text())
        return {k: str(v) for k, v in data.items() if v is not None}
    except Exception:
        return {}


def save_settings(values: Dict[str, Optional[str]]) -> Dict[str, str]:
    """Merge values into the settings file. None or "" removes a key."""
    current = load_settings()
    for key, value in values.items():
        if value is None or value == "":
            current.pop(key, None)
        else:
            current[key] = str(value).strip()
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2))
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return current


def api_key(service: str) -> Optional[str]:
    """Key for 'openai' or 'elevenlabs': environment variable first, then the settings file."""
    env = os.environ.get(ENV_KEYS.get(service, ""), "").strip()
    if env:
        return env
    return load_settings().get(f"{service}_api_key") or None


def setting(name: str, default: str = "") -> str:
    return load_settings().get(name, default)

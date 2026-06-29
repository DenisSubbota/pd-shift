from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "pd-shift"
CONFIG_FILE = CONFIG_DIR / "conf"

KEY_ALIASES = {
    "token": "token",
    "pd_token": "token",
    "team_id": "team_id",
    "pd_team_id": "team_id",
    "team": "team_id",
    "from_email": "from_email",
    "pd_from": "from_email",
    "from": "from_email",
}


def config_path() -> Path:
    return CONFIG_FILE


def load_config() -> dict[str, str]:
    """Load ~/.config/pd-shift/conf. Env vars still override these values."""
    if not CONFIG_FILE.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized = KEY_ALIASES.get(key.strip().lower())
        if normalized:
            values[normalized] = value.strip().strip('"').strip("'")
    return values


def config_value(name: str, *, env_names: tuple[str, ...]) -> str | None:
    for env_name in env_names:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return load_config().get(name) or None

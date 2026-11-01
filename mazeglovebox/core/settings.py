"""Per-user preferences, in ~/.config/maze-glovebox/settings.json."""
from __future__ import annotations

from mazeglovebox.core import atomic, paths

DEFAULTS = {
    "language": "en",       # "en" (default) or "tr"
}
LANGUAGES = ("en", "tr")


def _path():
    return paths.config_dir() / "settings.json"


def load() -> dict:
    out = dict(DEFAULTS)
    data = atomic.read_json(_path())
    if isinstance(data, dict):
        for k, v in data.items():
            if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                out[k] = v
    if out["language"] not in LANGUAGES:
        out["language"] = "en"
    return out


def save(**values) -> None:
    try:
        paths.config_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic.write_json(_path(), {**load(), **values})
    except OSError:
        pass

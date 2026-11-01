"""Where Maze Glovebox keeps its boxes and its list of open sessions.

Everything lives in the user's runtime directory: tmpfs, 0700, emptied at
logout. A copied file and whatever an app writes beside it are therefore
never written to disk, and cannot outlive the login even after a crash.
"""
from __future__ import annotations

import os
from pathlib import Path


def _private(path: Path, create: bool) -> Path:
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    return path


def run_dir(create: bool = False) -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return _private(Path(base) / "maze-glovebox", create)


def sessions_dir(create: bool = False) -> Path:
    return _private(run_dir(create) / "sessions", create)


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "maze-glovebox"

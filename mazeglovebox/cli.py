"""Command line and the entry points other programs use.

    maze-glovebox                      the window
    maze-glovebox FILE                 the window, with FILE ready to open
    maze-glovebox --open FILE          open FILE right away with its default app
                                       (Dolphin's right-click entry; falls back to
                                       the window when no app can run in the box)
    maze-glovebox --run FILE --with APP.desktop [--x11]
                                       one session in the foreground (what the
                                       window and --open start, detached)
    maze-glovebox --list               the files open in the box
    maze-glovebox --close ID           close one
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from mazeglovebox.core import handlers, session, settings
from mazeglovebox.gui.i18n import set_language, t

CLI_FLAGS = ("--open", "--run", "--list", "--close", "--version", "-h", "--help")


def wants_cli(argv: list[str]) -> bool:
    return any(a in CLI_FLAGS for a in argv)


def notify(key: str, **kw) -> None:
    text = t(f"n_{key}", **kw)
    print(text, file=sys.stderr, flush=True)
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "--app-name=Maze Glovebox", "--icon=maze-glovebox",
                            *(["--urgency=critical"] if key in ("failed", "error") else []),
                            "Maze Glovebox", text], timeout=5, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass


def self_argv() -> list[str]:
    installed = shutil.which("maze-glovebox")
    if installed:
        return [installed]
    return [sys.executable, "-P", str(Path(__file__).resolve().parents[1] / "main.py")]


def spawn_session(file: str, desktop_path: str, x11: bool = False) -> None:
    """Start one session detached; it outlives the window that started it."""
    root = str(Path(__file__).resolve().parents[1])
    subprocess.Popen([*self_argv(), "--run", file, "--with", desktop_path,
                      *(["--x11"] if x11 else [])],
                     cwd=str(Path.home()), env=dict(os.environ, PYTHONPATH=root),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _arg(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv: list[str]) -> int:
    set_language(settings.load()["language"])
    for a in argv:
        if a.startswith("--lang="):
            set_language(a.split("=", 1)[1])
    if "--version" in argv:
        from mazeglovebox import __version__
        print(f"maze-glovebox {__version__}")
        return 0
    if "-h" in argv or "--help" in argv:
        print(__doc__.strip())
        return 0
    if "--list" in argv:
        print(json.dumps([r.__dict__ for r in session.list_sessions()], indent=2))
        return 0
    if "--close" in argv:
        return 0 if session.close(_arg(argv, "--close") or "") else 1

    if "--run" in argv:
        file, desktop = _arg(argv, "--run"), _arg(argv, "--with")
        h = handlers.handler_from_desktop(desktop) if desktop else None
        if not file or h is None:
            print("maze-glovebox: --run FILE --with APP.desktop", file=sys.stderr)
            return 2
        try:
            return session.run(file, h, force_x11="--x11" in argv,
                               notify=lambda k, **kw: notify(k, **kw))
        except session.SessionError as e:
            notify("error", detail=t(f"e_{e.reason}"))
            return 1

    if "--open" in argv:
        file = _arg(argv, "--open")
        if not file or not os.path.isfile(file):
            notify("error", detail=t("e_unreadable"))
            return 1
        _mime, found = handlers.handlers_for(file)
        usable = [h for h in found if h.usable]
        if not usable:
            notify("no_handler", file=os.path.basename(file))
            # Let the user pick: the window opens with this file ready.
            subprocess.Popen([*self_argv(), os.path.abspath(file)], start_new_session=True)
            return 1
        spawn_session(os.path.abspath(file), usable[0].path)
        return 0
    return 0

"""One file, opened in one Glovebox, for as long as its app stays open.

A session is a process of its own (`maze-glovebox --run FILE`), started
detached by the window, by Dolphin's right-click entry or by the command
line. It:

  1. copies the file into a fresh home folder in the user's runtime directory
     (tmpfs, 0700) — the original is only ever read, once;
  2. starts the app in the box; an app that turns out to speak only X11 is
     restarted once with a separate X11 window of its own (x11run.sh);
  3. lists itself in sessions/<id>.json so the window can show and close it;
  4. when the app is gone, deletes the folder — the copy and anything the
     app wrote — and its entry.

Closing a session (SIGTERM) ends the app with it: bwrap runs with
--die-with-parent.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from mazeglovebox.core import paths, sandbox
from mazeglovebox.core.handlers import Handler

# An app that exits this fast, unsuccessfully, complaining about the display,
# could not open a Wayland window: try again with a separate X11 window.
_RETRY_WINDOW = 8.0
_X11_HINTS = ("could not connect to display", "cannot open display", "xcb",
              "no display", "unable to open display", "can't open display",
              "could not connect to any x display")


class SessionError(Exception):
    """reason: no_wayland | no_bwrap | too_big | unreadable | unusable_app"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass
class Record:
    id: str
    pid: int
    file: str
    app: str
    mode: str = "wayland"
    started: float = field(default_factory=time.time)
    # The kernel's start time of `pid`: a pid reused by another process after
    # a crash has a different one, so a stale entry is never taken for live.
    pid_start: str = ""

    def write(self) -> None:
        d = paths.sessions_dir(create=True)
        tmp = d / f".{self.id}.tmp"
        tmp.write_text(json.dumps(self.__dict__))
        os.replace(tmp, d / f"{self.id}.json")

    def remove(self) -> None:
        try:
            (paths.sessions_dir() / f"{self.id}.json").unlink()
        except FileNotFoundError:
            pass


def list_sessions() -> list[Record]:
    """Live sessions. An entry whose process is gone (crash, kill -9) is
    stale: its folder and entry are cleaned up here."""
    out = []
    d = paths.sessions_dir()
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            r = Record(**json.loads(f.read_text()))
        except (OSError, ValueError, TypeError):
            continue
        if _alive(r):
            out.append(r)
        else:
            r.remove()
            shutil.rmtree(paths.run_dir() / "boxes" / r.id, ignore_errors=True)
    return out


def process_start(pid: int) -> str:
    """Field 22 of /proc/PID/stat: when the process started, in clock ticks."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def _alive(r: "Record") -> bool:
    start = process_start(r.pid)
    return bool(start) and start == r.pid_start


def close(session_id: str) -> bool:
    for r in list_sessions():
        if r.id == session_id:
            try:
                os.kill(r.pid, signal.SIGTERM)
                return True
            except OSError:
                return False
    return False


def check_system() -> None:
    if not shutil.which("bwrap"):
        raise SessionError("no_bwrap")
    if not sandbox.wayland_socket():
        raise SessionError("no_wayland")


def stage_file(src: Path, box: Path) -> Path:
    """Copy the file into the box's home. Returns its path inside the box."""
    home = box / "home"
    home.mkdir(parents=True, mode=0o700)
    size = src.stat().st_size
    free = shutil.disk_usage(home).free
    if size > free - 64 * 1024 * 1024:
        raise SessionError("too_big", f"{size} bytes")
    dest = home / src.name
    try:
        shutil.copyfile(src, dest, follow_symlinks=True)
    except OSError as e:
        raise SessionError("unreadable", str(e)) from e
    os.chmod(dest, 0o600)
    return Path(sandbox.HOME) / src.name


def _looks_like_x11_only(stderr: str) -> bool:
    low = stderr.lower()
    return any(h in low for h in _X11_HINTS)


def run(file: str | Path, handler: Handler, force_x11: bool = False,
        notify=None) -> int:
    """Open `file` with `handler` in a Glovebox and wait for it to close."""
    notify = notify or (lambda *_a, **_k: None)
    check_system()
    if not handler.usable:
        raise SessionError("unusable_app", handler.reason)
    src = Path(file).resolve()
    if not src.is_file():
        raise SessionError("unreadable", str(src))

    sid = secrets.token_hex(5)
    boxes = paths.run_dir(create=True) / "boxes"
    boxes.mkdir(mode=0o700, exist_ok=True)
    box = boxes / sid
    record = Record(id=sid, pid=os.getpid(), file=str(src), app=handler.name,
                    mode="x11" if force_x11 else "wayland",
                    pid_start=process_start(os.getpid()))
    stop = {"flag": False}
    proc: subprocess.Popen | None = None

    def on_term(_sig, _frame) -> None:
        stop["flag"] = True
        if proc is not None and proc.poll() is None:
            proc.terminate()
    old = {s: signal.signal(s, on_term) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}

    try:
        inside = stage_file(src, box)
        record.write()
        notify("opened", app=handler.name, file=src.name)
        x11 = force_x11
        while not stop["flag"]:
            argv = sandbox.bwrap_argv(box / "home", handler.argv(str(inside)), x11=x11)
            err = tempfile.TemporaryFile()
            started = time.time()
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=err)
            code = proc.wait()
            err.seek(0)
            stderr = err.read()[-8000:].decode(errors="replace")
            err.close()
            if (not x11 and not stop["flag"] and code != 0
                    and time.time() - started < _RETRY_WINDOW
                    and _looks_like_x11_only(stderr)):
                x11 = True
                record.mode = "x11"
                record.write()
                continue
            if code not in (0, None) and not stop["flag"] and time.time() - started < _RETRY_WINDOW:
                lines = stderr.strip().splitlines()
                notify("failed", app=handler.name,
                       detail=lines[-1][:200] if lines else f"exit {code}")
            return 0 if stop["flag"] else code
        return 0
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        record.remove()
        shutil.rmtree(box, ignore_errors=True)

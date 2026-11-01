"""Which app opens a file inside the Glovebox.

The user's own default for the file type comes first (what Dolphin would
open it with), then every other installed app that declares the type. An app
is only offered if it can actually run inside the box:

  * its program must live under /usr or /opt — the only parts of the system
    the box sees;
  * it must not be a Flatpak or Snap: those start through their own runtime
    and portal services, which do not exist inside the box.
"""
from __future__ import annotations

import mimetypes
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_FIELD_CODES = {"%d", "%D", "%n", "%N", "%i", "%c", "%k", "%v", "%m"}
_FILE_CODES = {"%f", "%F", "%u", "%U"}
SYSTEM_PREFIXES = ("/usr/", "/opt/")


@dataclass
class Handler:
    desktop_id: str
    name: str
    path: str
    exec_line: str
    icon: str = ""
    mimes: list[str] = field(default_factory=list)
    reason: str = ""            # why it cannot run in the box; "" = it can
    hidden: bool = False        # NoDisplay: a helper entry, not an app in the menu

    @property
    def usable(self) -> bool:
        return not self.reason

    def argv(self, file_in_box: str) -> list[str]:
        """The command line with the file put where the Exec line wants it."""
        args = shlex.split(self.exec_line)
        out, placed = [], False
        for a in args:
            if a in _FILE_CODES:
                if not placed:
                    out.append(file_in_box)
                    placed = True
            elif a in _FIELD_CODES:
                continue
            else:
                out.append(a.replace("%%", "%"))
        if not placed:
            out.append(file_in_box)
        return out


def desktop_dirs() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = [data_home] + (os.environ.get("XDG_DATA_DIRS") or
                          "/usr/local/share:/usr/share").split(":")
    return [Path(d) / "applications" for d in dirs if d]


def _read_entry(path: Path, lang: str) -> dict | None:
    fields: dict[str, str] = {}
    in_entry = False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("["):
                    if in_entry:
                        break
                    in_entry = line == "[Desktop Entry]"
                    continue
                if in_entry and "=" in line:
                    k, v = line.split("=", 1)
                    fields.setdefault(k.strip(), v.strip())
    except OSError:
        return None
    if fields.get("Type", "Application") != "Application" or not fields.get("Exec"):
        return None
    if fields.get("Hidden") == "true":
        return None
    fields["_name"] = fields.get(f"Name[{lang}]") or fields.get("Name") or path.stem
    return fields


def _unusable_reason(exec_line: str) -> str:
    try:
        args = shlex.split(exec_line)
    except ValueError:
        return "unreadable"
    if not args:
        return "unreadable"
    prog = args[0]
    if args[0] == "env":
        rest = [a for a in args[1:] if "=" not in a]
        prog = rest[0] if rest else ""
    base = os.path.basename(prog)
    if base in ("flatpak", "snap") or "/snap/" in prog:
        return "sandboxed_package"
    resolved = prog if os.path.isabs(prog) else shutil.which(prog) or ""
    if not resolved:
        return "missing"
    resolved = os.path.realpath(resolved)
    if not resolved.startswith(SYSTEM_PREFIXES):
        return "outside_system"
    return ""


def handler_from_desktop(path: str | Path, lang: str = "en") -> Handler | None:
    path = Path(path)
    f = _read_entry(path, lang)
    if f is None:
        return None
    return Handler(desktop_id=path.name, name=f["_name"], path=str(path),
                   exec_line=f["Exec"], icon=f.get("Icon", ""),
                   mimes=[m for m in f.get("MimeType", "").split(";") if m],
                   reason=_unusable_reason(f["Exec"]),
                   hidden=f.get("NoDisplay") == "true")


def find_desktop(desktop_id: str) -> Path | None:
    for d in desktop_dirs():
        p = d / desktop_id
        if p.is_file():
            return p
    return None


def mime_type(path: str | Path) -> str:
    """The file's MIME type the way the desktop sees it."""
    for cmd in (["xdg-mime", "query", "filetype", str(path)],
                ["file", "--brief", "--mime-type", str(path)]):
        if shutil.which(cmd[0]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True,
                                     timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                out = ""
            if "/" in out and " " not in out:
                return out.split(";")[0]
    guess, _ = mimetypes.guess_type(str(path))
    return guess or "application/octet-stream"


def default_desktop_id(mime: str) -> str:
    if not shutil.which("xdg-mime"):
        return ""
    try:
        return subprocess.run(["xdg-mime", "query", "default", mime],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _parents(mime: str) -> list[str]:
    """Broader types an app may declare instead: text/x-python → text/plain."""
    out = [mime]
    major = mime.split("/")[0]
    if major == "text" and mime != "text/plain":
        out.append("text/plain")
    if mime in ("application/json", "application/xml", "application/x-shellscript",
                "application/javascript", "application/x-yaml"):
        out.append("text/plain")
    return out


def handlers_for(path: str | Path, lang: str = "en") -> tuple[str, list[Handler]]:
    """(mime type, handlers) — the default first, unusable ones last."""
    mime = mime_type(path)
    wanted = set(_parents(mime))
    seen: dict[str, Handler] = {}
    for d in desktop_dirs():
        try:
            files = sorted(d.glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            if f.name in seen:
                continue
            h = handler_from_desktop(f, lang)
            if h is not None and wanted.intersection(h.mimes):
                seen[f.name] = h
    default = default_desktop_id(mime)
    if default and default not in seen:
        p = find_desktop(default)
        h = handler_from_desktop(p, lang) if p else None
        if h is not None:
            seen[default] = h

    # Menu-hidden entries (KParts helpers and the like) are only offered when
    # the user chose one as the default; otherwise the same app shows twice.
    found = [h for h in seen.values() if not h.hidden or h.desktop_id == default]

    def rank(h: Handler) -> tuple:
        return (not h.usable, h.desktop_id != default, h.name.lower())
    return mime, sorted(found, key=rank)

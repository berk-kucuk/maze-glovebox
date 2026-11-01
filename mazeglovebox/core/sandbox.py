"""The box itself: a bubblewrap command line.

Inside, the app sees:
  * the system read-only (/usr, /etc, /opt) — so it can run, and change nothing;
  * a home folder that holds only a COPY of the file, on tmpfs, deleted when
    the app closes — your real home, keys, browser profiles and other files
    are simply not there;
  * a private /tmp and /run, its own process list, and no other user's anything.

It does not get:
  * a network — not even loopback to the host (fresh network namespace);
  * the session or system D-Bus — no notifications, no portals, no talking
    to other apps, and no handing the file to a copy of the app that is
    already running outside the box;
  * sound, the GPU, or the real X server (see x11run.sh);
  * any capability: --cap-drop ALL, and --new-session so it cannot type into
    the terminal that started it.

Only the Wayland socket goes in, so a window can appear. Wayland, unlike X11,
does not let one client read another's windows or keystrokes. The theme files
(colors, fonts) are mounted read-only so the app looks like the rest of the
desktop; there is nothing in them worth stealing, and no network to send it on.
"""
from __future__ import annotations

import os
from pathlib import Path

HOME = "/home/glovebox"
X11RUN = Path(__file__).with_name("x11run.sh")
BOX_X11RUN = "/run/glovebox/x11run.sh"

# Read-only theme and font settings, so the app looks right. Relative to ~.
_THEME_FILES = (
    ".config/kdeglobals",
    ".config/gtk-3.0/settings.ini",
    ".config/gtk-4.0/settings.ini",
    ".config/fontconfig",
    ".config/Trolltech.conf",
)

_LOCALE_VARS = ("LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
                "LC_NUMERIC", "LC_TIME", "LC_COLLATE", "LC_MONETARY", "LC_PAPER")


def wayland_socket() -> str | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    name = os.environ.get("WAYLAND_DISPLAY", "")
    if not runtime or not name:
        return None
    path = name if os.path.isabs(name) else os.path.join(runtime, name)
    return path if os.path.exists(path) else None


def bwrap_argv(stage: Path, app_argv: list[str], x11: bool = False,
               wayland: str | None = None) -> list[str]:
    uid = os.getuid()
    run_user = f"/run/user/{uid}"
    wayland = wayland if wayland is not None else wayland_socket()
    if not wayland:
        raise RuntimeError("no Wayland session")

    argv = [
        "bwrap",
        "--unshare-all",               # user, pid, net, ipc, uts, cgroup
        "--die-with-parent",
        "--new-session",
        "--cap-drop", "ALL",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/bin", "/sbin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib", "/lib64",
        "--ro-bind", "/etc", "/etc",
        "--ro-bind-try", "/opt", "/opt",
        "--ro-bind-try", "/var/cache/fontconfig", "/var/cache/fontconfig",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", "/tmp/.X11-unix", "--chmod", "1777", "/tmp/.X11-unix",
        "--tmpfs", "/run",
        "--dir", run_user, "--chmod", "0700", run_user,
        "--ro-bind", wayland, f"{run_user}/wayland-0",
        "--dir", "/home",
        "--bind", str(stage), HOME,
        "--chdir", HOME,
        "--hostname", "glovebox",
        "--clearenv",
        "--setenv", "HOME", HOME,
        "--setenv", "USER", "glovebox",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/opt/bin",
        "--setenv", "XDG_RUNTIME_DIR", run_user,
        "--setenv", "WAYLAND_DISPLAY", "wayland-0",
        "--setenv", "XDG_SESSION_TYPE", "wayland",
        "--setenv", "QT_QPA_PLATFORM", "wayland",
        "--setenv", "GDK_BACKEND", "wayland",
        "--setenv", "MOZ_ENABLE_WAYLAND", "1",
        "--setenv", "ELECTRON_OZONE_PLATFORM_HINT", "wayland",
        "--setenv", "QT_QUICK_BACKEND", "software",
        "--setenv", "LIBGL_ALWAYS_SOFTWARE", "1",
        "--setenv", "GLOVEBOX", "1",
    ]
    for var in _LOCALE_VARS:
        if os.environ.get(var):
            argv += ["--setenv", var, os.environ[var]]
    if os.environ.get("XDG_CURRENT_DESKTOP"):
        argv += ["--setenv", "XDG_CURRENT_DESKTOP", os.environ["XDG_CURRENT_DESKTOP"]]
    home = Path.home()
    for rel in _THEME_FILES:
        src = home / rel
        if src.exists() and not src.is_symlink():
            argv += ["--ro-bind", str(src), f"{HOME}/{rel}"]
    if x11:
        argv += ["--ro-bind", str(X11RUN), BOX_X11RUN, "--", "/bin/bash", BOX_X11RUN]
    else:
        argv += ["--"]
    return argv + list(app_argv)

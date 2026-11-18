"""The box itself: a bubblewrap command line.

Inside, the app sees:
  * the system read-only (/usr, /opt) — so it can run, and change nothing;
  * of /etc only what programs need to start and look right (see _ETC_ALLOW),
    and a made-up identity: its own passwd/group with one user, hostname,
    hosts and a fresh machine-id per box. The real /etc says who you are and
    what you run — user names, the machine id, VPN, Tor, wallet and Maze
    configs — and a crafted file could hide that in what you save from it;
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
    the terminal that started it;
  * user namespaces of its own (--disable-userns), or the kernel interfaces
    a document viewer never needs and most kernel exploits go through: a
    seccomp filter (see seccomp_filter) refuses them. A viewer that a crafted
    file takes over is then still boxed in by the kernel's front door, not
    only by the walls bwrap builds.

Only a Wayland socket goes in, so a window can appear — one of the box's own,
registered with the compositor as sandboxed (wlcontext.py), so privileged
protocols like fake input, clipboard managers and screen capture are hidden. Wayland, unlike X11,
does not let one client read another's windows or keystrokes. The theme files
(colors, fonts) are mounted read-only so the app looks like the rest of the
desktop; there is nothing in them worth stealing, and no network to send it on.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import shutil
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

# What of the real /etc the box sees, read-only. Everything here is what a
# program needs to start, find its libraries and look right — nothing that
# names the user or the machine. Glob patterns cover versioned directories.
_ETC_ALLOW = (
    "ld.so.cache", "ld.so.conf", "ld.so.conf.d",
    "nsswitch.conf", "host.conf", "services", "protocols",
    "localtime", "timezone", "locale.conf",
    "os-release", "lsb-release", "arch-release",
    "fonts", "xdg", "X11", "gtk-2.0", "gtk-3.0", "gtk-4.0", "dconf",
    "mime.types", "mailcap", "papersize", "paperspecs", "libpaper.d",
    "ssl", "ca-certificates", "pki", "drirc",
    "ImageMagick-*", "java-*", "libreoffice", "mpv", "vlc", "texmf",
    "kderc", "kde5rc", "kde6rc", "Trolltech.conf",
)


def _etc_entries() -> list[str]:
    import glob
    seen: list[str] = []
    for pat in _ETC_ALLOW:
        for path in sorted(glob.glob(os.path.join("/etc", pat))):
            if os.path.lexists(path) and path not in seen:
                seen.append(path)
    return seen


def identity_files(where: Path, uid: int, gid: int) -> dict[str, Path]:
    """The made-up /etc identity of one box, written under `where`.

    One user ("glovebox", the box's own uid so file ownership still reads
    right), no real names, a hostname that says nothing and a random
    machine-id — fresh for every box, so two boxes cannot be linked either.
    """
    where.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = {
        "passwd": (f"root:x:0:0::/root:/usr/bin/nologin\n"
                   f"glovebox:x:{uid}:{gid}::{HOME}:/bin/bash\n"
                   f"nobody:x:65534:65534:Nobody:/:/usr/bin/nologin\n"),
        "group": f"root:x:0:\nglovebox:x:{gid}:\nnobody:x:65534:\n",
        "hostname": "glovebox\n",
        "hosts": "127.0.0.1 localhost glovebox\n::1 localhost glovebox\n",
        "machine-id": os.urandom(16).hex() + "\n",
    }
    out = {}
    for name, text in files.items():
        path = where / name
        path.unlink(missing_ok=True)      # read-only from the previous try
        path.write_text(text)
        path.chmod(0o444)
        out[name] = path
    return out


_LOCALE_VARS = ("LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
                "LC_NUMERIC", "LC_TIME", "LC_COLLATE", "LC_MONETARY", "LC_PAPER")


# What the filter refuses. EPERM, as for anything else the box may not do;
# io_uring answers ENOSYS ("not implemented"), which is what makes libraries
# quietly fall back to ordinary reads and writes.
_SECCOMP_EPERM = (
    # Watching or steering other processes.
    "ptrace", "process_vm_readv", "process_vm_writev",
    # The usual ways into the kernel from an unprivileged process.
    "bpf", "perf_event_open", "userfaultfd",
    "keyctl", "add_key", "request_key",
    # Nothing in a box loads modules, boots kernels or manages the machine.
    "init_module", "finit_module", "delete_module", "kexec_load", "kexec_file_load",
    "reboot", "swapon", "swapoff", "acct", "syslog", "quotactl", "quotactl_fd",
    "vhangup", "lookup_dcookie", "nfsservctl", "fanotify_init",
    "name_to_handle_at", "open_by_handle_at",
    # Mounts: the old interface and the new one.
    "mount", "umount2", "pivot_root", "move_mount", "open_tree", "fsopen",
    "fsconfig", "fsmount", "fspick", "mount_setattr",
)
_SECCOMP_ENOSYS = ("io_uring_setup", "io_uring_enter", "io_uring_register")

_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_ERRNO = 0x00050000


def seccomp_filter() -> int:
    """A file descriptor with the compiled filter, for ``bwrap --seccomp FD``.

    Built with libseccomp (a dependency of systemd, so always there), which
    also takes care of the architecture check: a syscall made through another
    ABI — 32-bit int 0x80, x32 — matches no rule of this filter's
    architecture and kills the process instead of slipping past.
    The descriptor is close-on-exec; hand it to the child with pass_fds.
    Raises RuntimeError when the filter cannot be built: the box does not
    open without it.
    """
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2")
    except OSError as e:
        raise RuntimeError("libseccomp is not available, so the box cannot be sealed") from e
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    # The non-variadic twin of seccomp_rule_add (ctypes and C varargs do not mix).
    lib.seccomp_rule_add_array.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                           ctypes.c_uint, ctypes.c_void_p]
    lib.seccomp_export_bpf.restype = ctypes.c_int
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]

    ctx = lib.seccomp_init(_SCMP_ACT_ALLOW)
    if not ctx:
        raise RuntimeError("could not create the seccomp filter")
    fd = -1
    try:
        for names, err in ((_SECCOMP_EPERM, errno.EPERM), (_SECCOMP_ENOSYS, errno.ENOSYS)):
            for name in names:
                nr = lib.seccomp_syscall_resolve_name(name.encode())
                if nr < 0:
                    continue        # not a syscall on this architecture
                rc = lib.seccomp_rule_add_array(ctx, _SCMP_ACT_ERRNO | err, nr, 0, None)
                if rc < 0:
                    raise RuntimeError(f"seccomp rule for {name} failed ({-rc})")
        fd = os.memfd_create("glovebox-seccomp", os.MFD_CLOEXEC)
        rc = lib.seccomp_export_bpf(ctx, fd)
        if rc < 0:
            raise RuntimeError(f"could not export the seccomp filter ({-rc})")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        if fd >= 0:
            os.close(fd)
        raise
    finally:
        lib.seccomp_release(ctx)


def limited(argv: list[str]) -> list[str]:
    """Run the box in a cgroup of its own with a memory and process ceiling
    (audit GB4): a crafted file could otherwise fill RAM through the box's
    tmpfs or fork until the desktop falls over. systemd-run --scope execs the
    command itself, so it keeps the box's file descriptors (the seccomp
    filter) and is the process that signals reach. Without a user systemd
    the box runs as before, unlimited.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not shutil.which("systemd-run") or not runtime or not os.path.exists(os.path.join(runtime, "bus")):
        return argv
    try:
        with open("/proc/meminfo") as f:
            total_kib = next(int(l.split()[1]) for l in f if l.startswith("MemTotal:"))
    except (OSError, StopIteration, ValueError):
        total_kib = 8 * 1024 * 1024
    mem = max(1024 * 1024, total_kib // 2) * 1024          # half the RAM, at least 1 GiB
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect",
            "-p", f"MemoryMax={mem}", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=1024", "--"] + list(argv)


def wayland_socket() -> str | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    name = os.environ.get("WAYLAND_DISPLAY", "")
    if not runtime or not name:
        return None
    path = name if os.path.isabs(name) else os.path.join(runtime, name)
    return path if os.path.exists(path) else None


def bwrap_argv(stage: Path, app_argv: list[str], x11: bool = False,
               wayland: str | None = None, seccomp_fd: int | None = None) -> list[str]:
    """The bwrap command line. ``seccomp_fd`` (from :func:`seccomp_filter`) is
    required, and the caller must pass it to the child (``pass_fds``)."""
    if seccomp_fd is None:
        raise ValueError("a seccomp filter is required (see seccomp_filter)")
    uid = os.getuid()
    run_user = f"/run/user/{uid}"
    identity = identity_files(stage.parent / f".{stage.name}-etc", uid, os.getgid())
    wayland = wayland if wayland is not None else wayland_socket()
    if not wayland:
        raise RuntimeError("no Wayland session")

    argv = [
        "bwrap",
        "--unshare-all",               # user, pid, net, ipc, uts, cgroup
        "--unshare-user",              # required by --disable-userns
        "--disable-userns",            # no user namespaces of its own
        "--seccomp", str(seccomp_fd),
        "--die-with-parent",
        "--new-session",
        "--cap-drop", "ALL",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/bin", "/sbin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib", "/lib64",
        # /etc: an empty tmpfs, then only what is allowed (see _ETC_ALLOW),
        # then the made-up identity, then read-only.
        "--tmpfs", "/etc",
    ]
    for path in _etc_entries():
        argv += ["--ro-bind", path, path]
    for name, path in identity.items():
        argv += ["--ro-bind", str(path), f"/etc/{name}"]
    argv += [
        "--symlink", "../proc/self/mounts", "/etc/mtab",
        "--remount-ro", "/etc",
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

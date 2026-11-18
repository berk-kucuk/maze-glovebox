"""Core tests, against the real bubblewrap.

The box tests start a real Glovebox and ask, from inside, the questions an
attacker would: is there a network, where is the real home, is there a
D-Bus, which capabilities are left, which processes can be seen. No window
is opened: the Wayland "socket" is a placeholder file, and the apps are
shell commands.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mazeglovebox.core import handlers, paths, sandbox, session  # noqa: E402


def _bwrap_ok() -> bool:
    if not shutil.which("bwrap"):
        return False
    r = subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "true"],
                       capture_output=True)
    return r.returncode == 0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="glovebox-test-"))
        self.runtime = self.tmp / "run"
        self.runtime.mkdir(mode=0o700)
        (self.runtime / "wayland-test").write_text("")      # stands in for the socket
        self._env = mock.patch.dict(os.environ, {
            "XDG_RUNTIME_DIR": str(self.runtime), "WAYLAND_DISPLAY": "wayland-test",
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_DATA_HOME": str(self.tmp / "data")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def desktop(self, name: str, exec_line: str, mimes: str = "text/plain;") -> Path:
        d = self.tmp / "data" / "applications"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name}.desktop"
        p.write_text(f"[Desktop Entry]\nType=Application\nName={name}\n"
                     f"Exec={exec_line}\nMimeType={mimes}\n")
        return p


class HandlerTestCase(Base):
    def test_file_goes_where_the_exec_line_wants_it(self):
        h = handlers.Handler("x", "X", "", "okular --unique %U --icon %i")
        self.assertEqual(h.argv("/home/glovebox/a.pdf"),
                         ["okular", "--unique", "/home/glovebox/a.pdf", "--icon"])
        h = handlers.Handler("x", "X", "", "kate")
        self.assertEqual(h.argv("/f"), ["kate", "/f"])
        h = handlers.Handler("x", "X", "", "app %F %F")
        self.assertEqual(h.argv("/f"), ["app", "/f"], "the file is placed once")

    def test_apps_that_cannot_run_in_the_box_are_marked(self):
        flat = handlers.handler_from_desktop(
            self.desktop("flat", "/usr/bin/flatpak run org.x.App %f"))
        self.assertEqual(flat.reason, "sandboxed_package")
        home_app = self.tmp / "bin" / "tool"
        home_app.parent.mkdir()
        home_app.write_text("#!/bin/sh\n")
        home_app.chmod(0o755)
        outside = handlers.handler_from_desktop(self.desktop("home", f"{home_app} %f"))
        self.assertEqual(outside.reason, "outside_system")
        missing = handlers.handler_from_desktop(self.desktop("gone", "no-such-prog-xyz %f"))
        self.assertEqual(missing.reason, "missing")
        ok = handlers.handler_from_desktop(self.desktop("cat", "cat %f"))
        self.assertTrue(ok.usable)

    def test_handlers_for_puts_usable_ones_first(self):
        self.desktop("aaa-flat", "flatpak run x %f")
        self.desktop("zzz-cat", "cat %f")
        f = self.tmp / "notes.txt"
        f.write_text("hi")
        with mock.patch.object(handlers, "default_desktop_id", return_value=""):
            mime, found = handlers.handlers_for(f)
        ours = [h.desktop_id for h in found if h.desktop_id in ("aaa-flat.desktop",
                                                                "zzz-cat.desktop")]
        self.assertEqual(mime, "text/plain")
        self.assertEqual(ours, ["zzz-cat.desktop", "aaa-flat.desktop"])


@unittest.skipUnless(_bwrap_ok(), "bubblewrap cannot create namespaces here")
class BoxTestCase(Base):
    def inside(self, script: str) -> str:
        stage = self.tmp / "stage"
        stage.mkdir(exist_ok=True)
        (stage / "suspicious.pdf").write_text("%PDF fake")
        filt = sandbox.seccomp_filter()
        try:
            argv = sandbox.bwrap_argv(stage, ["bash", "-c", script], seccomp_fd=filt)
            r = subprocess.run(argv, capture_output=True, text=True, timeout=30, pass_fds=(filt,))
        finally:
            os.close(filt)
        return r.stdout + r.stderr

    def test_kernel_attack_surface_is_closed(self):
        """A taken-over viewer cannot reach the kernel interfaces most exploits use."""
        probe = (
            "import ctypes, errno\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            # x86_64 numbers: bpf, keyctl, perf_event_open, userfaultfd, io_uring_setup, ptrace
            "for name, nr in [('bpf', 321), ('keyctl', 250), ('perf_event_open', 298),\n"
            "                 ('userfaultfd', 323), ('io_uring_setup', 425), ('ptrace', 101)]:\n"
            "    ctypes.set_errno(0)\n"
            "    r = libc.syscall(nr, 0, 0, 0, 0, 0)\n"
            "    print(name, r, errno.errorcode.get(ctypes.get_errno(), ctypes.get_errno()))\n"
        )
        out = self.inside(f"python3 -c {shlex.quote(probe)}; "
                          "unshare -U true 2>/dev/null && echo USERNS-OPEN || echo userns-closed")
        for name in ("bpf", "keyctl", "perf_event_open", "userfaultfd", "ptrace"):
            self.assertIn(f"{name} -1 EPERM", out)
        self.assertIn("io_uring_setup -1 ENOSYS", out)
        self.assertIn("userns-closed", out)

    def test_the_box_never_opens_without_the_filter(self):
        with self.assertRaises(ValueError):
            sandbox.bwrap_argv(self.tmp, ["true"])

    def test_ordinary_work_still_runs(self):
        out = self.inside("python3 -c 'import os, threading, tempfile; "
                          "t = threading.Thread(target=print, args=(\"thread-ok\",)); t.start(); t.join(); "
                          "print(open(tempfile.mkstemp()[1], \"w\").write(\"x\"))'; cat ~/suspicious.pdf")
        self.assertIn("thread-ok", out)
        self.assertIn("%PDF fake", out)

    def test_no_network_at_all(self):
        out = self.inside("ls /sys/class/net; (exec 3<>/dev/tcp/1.1.1.1/53) 2>/dev/null "
                          "&& echo NET-LEAK || echo no-net")
        self.assertIn("no-net", out)
        self.assertNotIn("NET-LEAK", out)
        self.assertNotIn("wl", out.split("no-net")[0])
        self.assertNotIn("en", out.split("no-net")[0].replace("\n", " ").split())

    def test_only_the_copy_is_in_home_and_the_real_home_is_absent(self):
        real_home = str(Path.home())
        out = self.inside(f"ls -A ~; echo HOME=$HOME; ls /home; "
                          f"test -e '{real_home}' && echo REAL-HOME || echo no-real-home")
        self.assertIn("suspicious.pdf", out)
        self.assertIn("HOME=/home/glovebox", out)
        self.assertIn("no-real-home", out)

    def test_no_dbus_no_caps_no_other_processes_clean_env(self):
        out = self.inside("ls -A $XDG_RUNTIME_DIR; env | grep -c DBUS; "
                          "grep CapEff /proc/self/status; ls /proc | grep -c '^[0-9]'; "
                          "echo host=$(cat /proc/sys/kernel/hostname)")
        self.assertIn("wayland-0", out)
        self.assertNotIn("bus", out.split("\n")[0])
        self.assertIn("CapEff:\t0000000000000000", out)
        nprocs = int([l for l in out.splitlines() if l.strip().isdigit()][-1])
        self.assertLess(nprocs, 10, "the host's processes are not visible")
        self.assertIn("host=glovebox", out)

    def test_etc_names_neither_the_user_nor_the_machine(self):
        """GB2: the box used to see all of /etc — user names, machine-id,
        and which VPN, Tor, wallet and Maze tools the machine runs."""
        import getpass
        real_mid = Path("/etc/machine-id").read_text().strip()
        out = self.inside("cat /etc/machine-id /etc/hostname; cut -d: -f1 /etc/passwd; "
                          "whoami; ls /etc")
        self.assertNotIn(real_mid, out)
        self.assertNotIn(getpass.getuser(), out.replace("glovebox", ""))
        self.assertIn("glovebox", out)
        listing = set(out.split())
        for secret in ("shadow", "sudoers", "maze-cloak", "maze-ghost", "NetworkManager",
                       "wireguard", "ssh", "tor", "mullvad-vpn", "openvpn", "crypttab",
                       "fstab", "subuid"):
            self.assertNotIn(secret, listing, secret)
        self.assertIn("ld.so.cache", listing)

    def test_every_box_has_its_own_machine_id(self):
        a = self.inside("cat /etc/machine-id")
        b = self.inside("cat /etc/machine-id")
        self.assertRegex(a.strip(), r"^[0-9a-f]{32}$")
        self.assertNotEqual(a, b)

    def test_desktop_apps_still_start(self):
        checks = [(app, cmd) for app, cmd in (
            ("okular", "okular --version"), ("mpv", "mpv --version"),
            ("gwenview", "gwenview --version"), ("kate", "kate --version"))
            if shutil.which(app)]
        if not checks:
            self.skipTest("no desktop viewer installed")
        for app, cmd in checks:
            out = self.inside(f"QT_QPA_PLATFORM=offscreen {cmd} >/dev/null 2>/tmp/e; echo rc=$?; head -3 /tmp/e")
            self.assertIn("rc=0", out, f"{app}: {out}")

    def test_box_runs_with_a_memory_and_task_ceiling(self):
        """GB4: a crafted file must not be able to take the desktop's RAM.
        Read from outside: inside, the box has a cgroup namespace of its own."""
        from unittest import mock
        stage = self.tmp / "stage"
        stage.mkdir(exist_ok=True)
        filt = sandbox.seccomp_filter()
        try:
            box_argv = sandbox.bwrap_argv(stage, ["sleep", "5"], seccomp_fd=filt)
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}):
                argv = sandbox.limited(box_argv)
                if argv[0] != "systemd-run":
                    self.skipTest("no user systemd here")
                proc = subprocess.Popen(argv, pass_fds=(filt,), stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE)
        finally:
            os.close(filt)
        try:
            mine = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[-1]
            cg = mine
            for _ in range(100):        # until systemd-run has moved into its scope
                cg = Path(f"/proc/{proc.pid}/cgroup").read_text().strip().split("::", 1)[-1]
                if cg != mine and cg.endswith(".scope"):
                    break
                time.sleep(0.05)
            self.assertNotEqual(cg, mine, "the box stayed in our own cgroup")
            base = Path("/sys/fs/cgroup") / cg.lstrip("/")
            self.assertNotEqual((base / "memory.max").read_text().strip(), "max")
            self.assertEqual((base / "pids.max").read_text().strip(), "1024")
        finally:
            proc.kill()
            proc.wait(10)

    def test_staging_refuses_what_is_not_a_document(self):
        """GB5: a FIFO would hang the copy; /proc files are not documents."""
        box = self.tmp / "box"
        fifo = self.tmp / "pipe.pdf"
        os.mkfifo(fifo)
        for bad in (fifo, Path("/proc/self/environ")):
            with self.assertRaises(session.SessionError):
                session.stage_file(bad, box / bad.name.replace("/", "_"))
        link = self.tmp / "link.pdf"
        real = self.tmp / "real.pdf"
        real.write_bytes(b"%PDF real")
        link.symlink_to(real)
        inside = session.stage_file(link, self.tmp / "box2")
        self.assertEqual((self.tmp / "box2" / "home" / "link.pdf").read_bytes(), b"%PDF real")
        self.assertEqual(inside.name, "link.pdf")

    def test_system_is_read_only(self):
        out = self.inside("touch /etc/x 2>/dev/null && echo WROTE-ETC; "
                          "touch /usr/x 2>/dev/null && echo WROTE-USR; echo done")
        self.assertNotIn("WROTE", out)


@unittest.skipUnless(_bwrap_ok(), "bubblewrap cannot create namespaces here")
class SessionTestCase(Base):
    def test_a_session_copies_runs_and_cleans_up(self):
        original = self.tmp / "invoice.pdf"
        original.write_text("ORIGINAL")
        h = handlers.handler_from_desktop(self.desktop(
            "probe", "bash -c 'cat \"$0\" > ~/seen; echo changed > \"$0\"; sleep 1' %f"))
        seen_records = []

        def watch():
            for _ in range(40):
                seen_records.extend(session.list_sessions())
                if seen_records:
                    return
                time.sleep(0.05)
        w = threading.Thread(target=watch)
        w.start()
        code = session.run(original, h)
        w.join()
        self.assertEqual(code, 0)
        self.assertEqual(original.read_text(), "ORIGINAL", "the original is untouched")
        self.assertTrue(seen_records, "the session listed itself while open")
        self.assertEqual(seen_records[0].file, str(original))
        self.assertEqual(session.list_sessions(), [])
        self.assertEqual(list((paths.run_dir() / "boxes").iterdir()), [],
                         "the copy and what the app wrote are gone")

    def test_x11_only_app_is_retried_in_its_own_x_window(self):
        f = self.tmp / "doc.docx"
        f.write_text("x")
        h = handlers.handler_from_desktop(self.desktop(
            "x11only", "bash -c 'echo \"Could not connect to any X display.\" >&2; "
                       "exit 1' %f"))
        modes = []
        real = sandbox.bwrap_argv

        def spy(stage, argv, x11=False, wayland=None, seccomp_fd=None):
            modes.append(x11)
            if x11:          # do not start a real Xwayland in a test
                return real(stage, ["true"], x11=False, wayland=wayland, seccomp_fd=seccomp_fd)
            return real(stage, argv, x11=x11, wayland=wayland, seccomp_fd=seccomp_fd)
        with mock.patch.object(sandbox, "bwrap_argv", side_effect=spy):
            session.run(f, h)
        self.assertEqual(modes, [False, True])

    def test_close_ends_the_app(self):
        f = self.tmp / "a.txt"
        f.write_text("x")
        h = handlers.handler_from_desktop(self.desktop("sleeper", "bash -c 'sleep 30' %f"))
        p = subprocess.Popen([sys.executable, "-P", str(ROOT / "main.py"), "--run", str(f),
                              "--with", h.path], env=dict(os.environ, PYTHONPATH=str(ROOT)))
        for _ in range(100):
            recs = session.list_sessions()
            if recs:
                break
            time.sleep(0.05)
        self.assertTrue(recs)
        self.assertTrue(session.close(recs[0].id))
        self.assertEqual(p.wait(timeout=10), 0)

    def test_a_dead_sessions_entry_is_cleaned_up(self):
        r = session.Record(id="dead", pid=os.getpid(), file="/x", app="A",
                           pid_start="1")      # same pid, different start: reused pid
        r.write()
        (paths.run_dir() / "boxes" / "dead").mkdir(parents=True)
        self.assertEqual(session.list_sessions(), [])
        self.assertFalse((paths.run_dir() / "boxes" / "dead").exists())
        self.assertEqual(session.list_sessions(), [])

    def test_unusable_app_is_refused(self):
        f = self.tmp / "a.txt"
        f.write_text("x")
        h = handlers.handler_from_desktop(self.desktop("flat", "flatpak run x %f"))
        with self.assertRaises(session.SessionError) as cm:
            session.run(f, h)
        self.assertEqual(cm.exception.reason, "unusable_app")


class I18nTestCase(unittest.TestCase):
    def test_same_keys_and_placeholders(self):
        import re
        from mazeglovebox.gui import i18n
        en, tr = i18n.STRINGS["en"], i18n.STRINGS["tr"]
        self.assertEqual(set(en), set(tr))
        for k in en:
            names = set(re.findall(r"{(\w+)}", en[k]))
            self.assertEqual(names, set(re.findall(r"{(\w+)}", tr[k])), k)
            for lang in ("en", "tr"):
                i18n.set_language(lang)
                i18n.t(k, **{n: "x" for n in names})
        i18n.set_language("en")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class WaylandContextTestCase(unittest.TestCase):
    """GB3: the box's Wayland socket is marked as sandboxed, so the compositor
    hides its privileged protocols (clipboard snooping, fake input, screen
    capture) from whatever runs inside. Needs a live compositor that offers
    security-context-v1 (KWin 6 does)."""

    def test_privileged_globals_are_hidden(self):
        import tempfile as _t
        from mazeglovebox.core import wlcontext
        rt = f"/run/user/{os.getuid()}"
        display = os.environ.get("WAYLAND_DISPLAY", "")
        real = os.path.join(rt, display) if display and not os.path.isabs(display) else display
        if not real or not os.path.exists(real) or not shutil.which("wayland-info"):
            self.skipTest("no Wayland session")
        d = Path(_t.mkdtemp(dir=rt))
        try:
            try:
                ctx = wlcontext.create(real, d / "wl.sock", "org.mazelinux.glovebox.test", "t")
            except wlcontext.Unsupported as e:
                self.skipTest(f"compositor without security-context-v1: {e}")

            def names(path):
                out = subprocess.run(["wayland-info"], env=dict(os.environ, WAYLAND_DISPLAY=str(path)),
                                     capture_output=True, text=True, timeout=10).stdout
                return {l.split("'")[1] for l in out.splitlines() if "interface: '" in l}
            normal, boxed = names(real), names(ctx.socket_path)
            self.assertIn("wl_compositor", boxed)
            self.assertNotIn("wp_security_context_manager_v1", boxed)
            self.assertLess(len(boxed), len(normal))
            for priv in ("org_kde_kwin_fake_input", "ext_data_control_manager_v1",
                         "zkde_screencast_unstable_v1"):
                if priv in normal:
                    self.assertNotIn(priv, boxed, priv)
            ctx.close()
            self.assertFalse(ctx.socket_path.exists())
        finally:
            shutil.rmtree(d, ignore_errors=True)

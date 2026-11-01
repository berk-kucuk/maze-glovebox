"""Core tests, against the real bubblewrap.

The box tests start a real Glovebox and ask, from inside, the questions an
attacker would: is there a network, where is the real home, is there a
D-Bus, which capabilities are left, which processes can be seen. No window
is opened: the Wayland "socket" is a placeholder file, and the apps are
shell commands.
"""
from __future__ import annotations

import os
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
        argv = sandbox.bwrap_argv(stage, ["bash", "-c", script])
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return r.stdout + r.stderr

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

        def spy(stage, argv, x11=False, wayland=None):
            modes.append(x11)
            if x11:          # do not start a real Xwayland in a test
                return real(stage, ["true"], x11=False, wayland=wayland)
            return real(stage, argv, x11=x11, wayland=wayland)
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

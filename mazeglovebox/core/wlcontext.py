"""A Wayland socket of the box's own, marked as sandboxed (audit GB3).

Handing the box the session's real Wayland socket made the app an ordinary
client: the compositor could not tell it from a trusted one, and KWin's
privileged protocols (screen capture, clipboard managers, window management)
were as open to it as to the desktop's own tools. The security-context-v1
protocol is how Flatpak avoids that: a client asks the compositor to accept
connections on a new socket, tagged with a sandbox engine and app id, and
every client that connects there is treated as sandboxed — KWin hides its
privileged globals from it.

No Wayland library is available to Maze's Python, so this speaks the few
messages it needs on the wire itself:

    wl_display.get_registry / sync, wl_registry.bind,
    wp_security_context_manager_v1.create_listener (+ two fds),
    wp_security_context_v1.set_sandbox_engine / set_app_id /
        set_instance_id / commit / destroy

The listener lives until the write end of ``close_fd`` is closed: the caller
keeps it open for as long as the box runs (see Context.close).
"""
from __future__ import annotations

import array
import os
import socket
import struct
from dataclasses import dataclass
from pathlib import Path

ENGINE = "org.mazelinux.glovebox"
_IFACE = "wp_security_context_manager_v1"

# Object ids this client allocates (wl_display is always 1).
_DISPLAY, _REGISTRY, _SYNC1, _MANAGER, _CONTEXT, _SYNC2 = 1, 2, 3, 4, 5, 6


class Unsupported(Exception):
    """The compositor has no security-context-v1 (or refused it)."""


def _string(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + b + b"\0" * (-len(b) % 4)


def _message(obj: int, opcode: int, payload: bytes = b"") -> bytes:
    return struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload


class _Conn:
    def __init__(self, path: str):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        self.sock.settimeout(5)
        self.sock.connect(path)
        self.buf = b""

    def send(self, data: bytes, fds: list[int] | None = None) -> None:
        if fds:
            anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds).tobytes())]
            self.sock.sendmsg([data], anc)
        else:
            self.sock.sendall(data)

    def events(self):
        """Yield (object, opcode, payload) until the caller stops."""
        while True:
            while len(self.buf) >= 8:
                obj, word = struct.unpack_from("<II", self.buf)
                size, opcode = word >> 16, word & 0xFFFF
                if size < 8:
                    raise Unsupported("malformed message from the compositor")
                if len(self.buf) < size:
                    break
                payload, self.buf = self.buf[8:size], self.buf[size:]
                yield obj, opcode, payload
            chunk = self.sock.recv(65536)
            if not chunk:
                raise Unsupported("the compositor closed the connection")
            self.buf += chunk

    def close(self) -> None:
        self.sock.close()


def _read_string(payload: bytes, off: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<I", payload, off)
    s = payload[off + 4:off + 4 + n].rstrip(b"\0").decode(errors="replace")
    return s, off + 4 + n + (-n % 4)


def _check(obj: int, opcode: int, payload: bytes) -> None:
    if obj == _DISPLAY and opcode == 0:              # wl_display.error
        _, code = struct.unpack_from("<II", payload)
        msg, _ = _read_string(payload, 8)
        raise Unsupported(f"compositor error {code}: {msg}")


@dataclass
class Context:
    """A live sandboxed listener. ``socket_path`` is what the box connects to."""
    socket_path: Path
    close_fd: int

    def close(self) -> None:
        """Ends the listener: the compositor stops accepting on the socket."""
        if self.close_fd >= 0:
            os.close(self.close_fd)
            self.close_fd = -1
        try:
            self.socket_path.unlink()
        except OSError:
            pass


def create(display_path: str, socket_path: Path, app_id: str, instance_id: str) -> Context:
    """Register ``socket_path`` with the compositor as a sandboxed listener.

    Raises Unsupported when the compositor does not offer security-context-v1
    (the caller then decides whether to fall back).
    """
    conn = _Conn(display_path)
    listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    close_r, close_w = os.pipe2(os.O_CLOEXEC)
    try:
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        listen.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        listen.listen(64)

        # Find the manager among the globals.
        conn.send(_message(_DISPLAY, 1, struct.pack("<I", _REGISTRY))     # get_registry
                  + _message(_DISPLAY, 0, struct.pack("<I", _SYNC1)))     # sync
        name = version = None
        for obj, opcode, payload in conn.events():
            _check(obj, opcode, payload)
            if obj == _REGISTRY and opcode == 0:                          # global
                (gname,) = struct.unpack_from("<I", payload)
                iface, off = _read_string(payload, 4)
                if iface == _IFACE:
                    name, version = gname, struct.unpack_from("<I", payload, off)[0]
            elif obj == _SYNC1 and opcode == 0:                           # callback.done
                break
        if name is None:
            raise Unsupported(f"the compositor does not offer {_IFACE}")

        conn.send(_message(_REGISTRY, 0, struct.pack("<I", name) + _string(_IFACE)
                           + struct.pack("<II", 1, _MANAGER)))             # bind
        conn.send(_message(_MANAGER, 1, struct.pack("<I", _CONTEXT)),     # create_listener
                  fds=[listen.fileno(), close_r])
        conn.send(_message(_CONTEXT, 1, _string(ENGINE))                  # set_sandbox_engine
                  + _message(_CONTEXT, 2, _string(app_id))                # set_app_id
                  + _message(_CONTEXT, 3, _string(instance_id))           # set_instance_id
                  + _message(_CONTEXT, 4)                                 # commit
                  + _message(_CONTEXT, 0)                                 # destroy (listener stays)
                  + _message(_DISPLAY, 0, struct.pack("<I", _SYNC2)))     # sync
        for obj, opcode, payload in conn.events():
            _check(obj, opcode, payload)
            if obj == _SYNC2 and opcode == 0:
                break
    except (OSError, struct.error) as e:
        os.close(close_w)
        try:
            socket_path.unlink()
        except OSError:
            pass
        raise Unsupported(str(e)) from e
    except Unsupported:
        os.close(close_w)
        try:
            socket_path.unlink()
        except OSError:
            pass
        raise
    finally:
        # The compositor has its own copies of both now.
        listen.close()
        os.close(close_r)
        conn.close()
    return Context(socket_path, close_w)

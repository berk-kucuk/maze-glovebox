#!/bin/bash
# Runs INSIDE the Glovebox, for apps that only speak X11.
#
# Giving the box the real X server would undo the box: every X11 client can
# read every other window and every key press. Instead the app gets an X
# server of its own — Xwayland, rootful, shown as one window on the desktop —
# started here, inside the same namespaces as the app, with no network, no
# GPU and nothing else connected to it.
set -u
DISPLAY_NUM=42
Xwayland ":$DISPLAY_NUM" -shm -decorate -geometry "${GLOVEBOX_X11_GEOMETRY:-1366x880}" \
    -nolisten tcp -terminate 2>/tmp/xwayland.log &
xpid=$!
for _ in $(seq 100); do
    [ -S "/tmp/.X11-unix/X$DISPLAY_NUM" ] && break
    kill -0 "$xpid" 2>/dev/null || break
    sleep 0.05
done
if [ ! -S "/tmp/.X11-unix/X$DISPLAY_NUM" ]; then
    echo "glovebox: the separate X11 window could not start" >&2
    cat /tmp/xwayland.log >&2
    exit 1
fi
export DISPLAY=":$DISPLAY_NUM"
unset WAYLAND_DISPLAY QT_QPA_PLATFORM GDK_BACKEND ELECTRON_OZONE_PLATFORM_HINT
"$@"
code=$?
kill "$xpid" 2>/dev/null
exit "$code"

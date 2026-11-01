"""Main window: the chamber.

Laid out like the thing it is named after. The middle of the window is the
box — a framed chamber the file is dropped into, where the app is picked and
the file opened. Along the left runs the rail of seals: what the app inside
cannot reach (or, switched over, what the box cannot do). Underneath sits
what is in the box right now, one tile per open file.

The window is only a front door: every file it opens runs as a session of its
own (`maze-glovebox --run`), which keeps going if the window is closed, and
the tiles are read back from the sessions every two seconds — the window
never keeps its own idea of what is open.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PyQt6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                             QLabel, QMainWindow, QPushButton, QSizePolicy, QVBoxLayout,
                             QWidget)

from mazeglovebox import __version__, cli
from mazeglovebox.core import handlers, session, settings
from mazeglovebox.core.handlers import Handler
from mazeglovebox.gui import worker
from mazeglovebox.gui.chrome import EdgeResizer, TitleBar, WindowButton, button, label
from mazeglovebox.gui.flow import FlowLayout
from mazeglovebox.gui.glass import GlassBackground, GlassFrame
from mazeglovebox.gui.glyphs import glyph_pixmap
from mazeglovebox.gui.i18n import language, set_language, t
from mazeglovebox.gui.icons import app_logo, logo_pixmap
from mazeglovebox.gui.pill import Pill

_SEALS = (("net_off", "sealed_net"), ("folder", "sealed_home"),
          ("apps_off", "sealed_apps"), ("trash", "sealed_trace"))
_LIMITS = ("limit_view", "limit_clipboard", "limit_save", "limit_kernel")


@dataclass
class Pending:
    path: Path
    mime: str = ""
    handlers: list[Handler] = field(default_factory=list)
    loaded: bool = False


def shown(text: str) -> str:
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


class Chamber(QFrame):
    """The box: a dark well with viewfinder brackets at its corners.

    Painted, so the frame reads as an enclosure rather than as one more card:
    a faint dashed wall, solid brackets on the corners, and a brighter rim
    while a file is dragged over the window.
    """

    def __init__(self):
        super().__init__()
        self.hot = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_hot(self, hot: bool) -> None:
        if hot != self.hot:
            self.hot = hot
            self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(r, 22, 22)
        p.fillPath(path, QColor(0, 0, 0, 175))
        wall = QPen(QColor(255, 255, 255, 70 if self.hot else 34), 1.2)
        wall.setStyle(Qt.PenStyle.DashLine)
        wall.setDashPattern([3, 5])
        p.setPen(wall)
        p.drawPath(path)
        # Corner brackets.
        arm, inset = 34.0, 10.0
        pen = QPen(QColor(242, 242, 244, 235 if self.hot else 170), 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        x0, y0 = r.left() + inset, r.top() + inset
        x1, y1 = r.right() - inset, r.bottom() - inset
        for (cx, cy, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1),
                                 (x1, y1, -1, -1)):
            p.drawLine(QPointF(cx, cy), QPointF(cx + dx * arm, cy))
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * arm))
        p.end()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Maze Glovebox")
        self.setWindowIcon(app_logo())
        self.resize(1060, 760)
        self.setMinimumSize(820, 600)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAcceptDrops(True)
        self._resizer = EdgeResizer(self)
        QApplication.instance().installEventFilter(self._resizer)

        self.pending: list[Pending] = []
        self.sessions: list[session.Record] = []
        self.rail_mode = "inside"
        self._problem = self._system_problem()
        self._render_timer = QTimer(self, singleShot=True)
        self._render_timer.timeout.connect(self._render)

        self._build_chrome()
        self._render()
        self._poll = QTimer(self, interval=2000)
        self._poll.timeout.connect(self._refresh_sessions)
        self._poll.start()
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_file)

    @staticmethod
    def _system_problem() -> str:
        try:
            session.check_system()
        except session.SessionError as e:
            return t(f"e_{e.reason}")
        return ""

    # ── chrome ─────────────────────────────────────────────────────────────
    def _build_chrome(self) -> None:
        root = GlassBackground()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        header = TitleBar(self)
        header.setFixedHeight(50)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 0, 0, 0)
        h.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(28))
        h.addWidget(logo)
        h.addWidget(label("MAZE GLOVEBOX", "wordmark"))
        h.addStretch(1)
        for code, text in (("en", "EN"), ("tr", "TR")):
            b = QPushButton(text, objectName="seg_on" if language() == code else "seg")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(t("tip_language"))
            b.clicked.connect(lambda _=False, c=code: self.set_lang(c))
            h.addWidget(b)
        sep = QFrame(objectName="vline")
        sep.setFixedSize(1, 22)
        h.addSpacing(4)
        h.addWidget(sep)
        self._max_btn = WindowButton("win_max", t("tip_maximize"))
        self._max_btn.clicked.connect(
            lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
        mn = WindowButton("win_min", t("tip_minimize"))
        mn.clicked.connect(self.showMinimized)
        cl = WindowButton("win_close", t("tip_close"))
        cl.clicked.connect(self.close)
        for b in (mn, self._max_btn, cl):
            h.addWidget(b)
        v.addWidget(header)

        self._body = QWidget()
        v.addWidget(self._body, 1)

        footer = QWidget(objectName="footer")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer.setFixedHeight(30)
        fh = QHBoxLayout(footer)
        fh.setContentsMargins(22, 0, 22, 0)
        self._status = label("", "statusbar")
        fh.addWidget(self._status, 1)
        fh.addWidget(label(f"Maze Glovebox {__version__}", "statusbar"))
        v.addWidget(footer)
        self.setCentralWidget(root)
        self._chamber: Chamber | None = None

    def changeEvent(self, ev) -> None:
        if ev.type() == QEvent.Type.WindowStateChange:
            self._max_btn.set_kind("win_restore" if self.isMaximized() else "win_max")
        super().changeEvent(ev)

    def set_lang(self, code: str) -> None:
        if code == language():
            return
        set_language(code)
        settings.save(language=code)
        self._problem = self._system_problem()
        QTimer.singleShot(0, self._rebuild)     # never inside the clicked button's slot

    def _rebuild(self) -> None:
        self._build_chrome()
        self._render()

    def schedule_render(self) -> None:
        self._render_timer.start(0)

    def set_rail(self, mode: str) -> None:
        self.rail_mode = mode
        self.schedule_render()

    # ── files ──────────────────────────────────────────────────────────────
    def choose_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, t("btn_choose"), str(Path.home()))
        if f:
            self.add_paths([f])

    def add_paths(self, paths: list[str]) -> None:
        for raw in paths:
            p = Path(raw)
            if not p.is_file() or any(x.path == p for x in self.pending):
                continue
            item = Pending(p)
            self.pending.insert(0, item)
            worker.run(handlers.handlers_for, p, on_done=lambda res, it=item: self._loaded(it, res),
                       on_fail=lambda _e, it=item: self._loaded(it, ("", [])), critical=False)
        self.schedule_render()

    def _loaded(self, item: Pending, res: tuple) -> None:
        item.mime, item.handlers = res
        item.loaded = True
        self.schedule_render()

    def open_pending(self, item: Pending, combo: QComboBox) -> None:
        h = combo.currentData()
        if not isinstance(h, Handler) or not h.usable:
            return
        cli.spawn_session(str(item.path), h.path)
        self.pending = [x for x in self.pending if x is not item]
        self.schedule_render()
        QTimer.singleShot(800, self._refresh_sessions)

    def remove_pending(self, item: Pending) -> None:
        self.pending = [x for x in self.pending if x is not item]
        self.schedule_render()

    def focus_pending(self, item: Pending) -> None:
        self.pending = [item] + [x for x in self.pending if x is not item]
        self.schedule_render()

    def close_session(self, sid: str) -> None:
        session.close(sid)
        QTimer.singleShot(600, self._refresh_sessions)

    def _refresh_sessions(self) -> None:
        recs = session.list_sessions()
        if [(r.id, r.mode) for r in recs] != [(r.id, r.mode) for r in self.sessions]:
            self.sessions = recs
            self.schedule_render()

    # ── drag & drop ────────────────────────────────────────────────────────
    def dragEnterEvent(self, ev) -> None:
        md = ev.mimeData()
        if md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            ev.acceptProposedAction()
            if self._chamber is not None:
                self._chamber.set_hot(True)

    def dragLeaveEvent(self, ev) -> None:
        if self._chamber is not None:
            self._chamber.set_hot(False)
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev) -> None:
        if self._chamber is not None:
            self._chamber.set_hot(False)
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            ev.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.add_paths(paths))

    # ── page ───────────────────────────────────────────────────────────────
    def _render(self) -> None:
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._rail())
        main = QVBoxLayout()
        main.setContentsMargins(22, 20, 22, 18)
        main.setSpacing(14)
        if self._problem:
            main.addWidget(self._warn_box(self._problem))
        self._chamber = self._chamber_widget()
        main.addWidget(self._chamber, 1)
        main.addWidget(self._in_the_box())
        row.addLayout(main, 1)

        layout = self.centralWidget().layout()
        layout.replaceWidget(self._body, body)
        self._body.hide()
        self._body.deleteLater()
        self._body = body
        n = len(self.sessions)
        self._status.setText(t("status_ready", n=n) if n else t("status_idle"))

    # The rail of seals ───────────────────────────────────────────────────
    def _rail(self) -> QWidget:
        rail = QWidget(objectName="rail")
        rail.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rail.setFixedWidth(250)
        v = QVBoxLayout(rail)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(14)
        seg = QHBoxLayout()
        seg.setSpacing(4)
        for mode, key in (("inside", "rail_inside"), ("limits", "rail_limits")):
            b = QPushButton(t(key), objectName="seg_on" if self.rail_mode == mode else "seg")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, m=mode: self.set_rail(m))
            seg.addWidget(b)
        seg.addStretch(1)
        v.addLayout(seg)

        if self.rail_mode == "inside":
            v.addWidget(label(t("sec_sealed"), "section", wrap=True))
            for icon, key in _SEALS:
                item = QHBoxLayout()
                item.setSpacing(11)
                ic = QLabel()
                ic.setPixmap(glyph_pixmap(icon, 22, "#f2f2f4"))
                ic.setFixedWidth(26)
                item.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                col = QVBoxLayout()
                col.setSpacing(2)
                col.addWidget(label(t(key), "seal_title", wrap=True))
                col.addWidget(label(t(key + "_d"), "seal_desc", wrap=True))
                item.addLayout(col, 1)
                v.addLayout(item)
        else:
            v.addWidget(label(t("sec_limits"), "section", wrap=True))
            for key in _LIMITS:
                item = QHBoxLayout()
                item.setSpacing(8)
                ic = QLabel()
                ic.setPixmap(glyph_pixmap("point", 13, "#8f8f95"))
                item.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                item.addWidget(label(t(key), "seal_desc", wrap=True), 1)
                v.addLayout(item)
        v.addStretch(1)
        return rail

    # The chamber ───────────────────────────────────────────────────────────
    def _chamber_widget(self) -> Chamber:
        ch = Chamber()
        v = QVBoxLayout(ch)
        v.setContentsMargins(56, 40, 56, 40)
        v.setSpacing(12)
        v.addStretch(1)
        if not self.pending:
            ic = QLabel()
            ic.setPixmap(glyph_pixmap("box", 64, "#f2f2f4"))
            v.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
            v.addSpacing(6)
            title = label(t("hero_title"), "chamber_title", wrap=True)
            title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(title)
            sub = label(t("hero_sub"), "chamber_sub", wrap=True)
            sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            # Centred by margins, not by alignment: a word-wrapped label given
            # AlignHCenter is sized to one line and cuts the rest off.
            sub_row = QHBoxLayout()
            sub_row.addStretch(1)
            sub.setFixedWidth(540)
            sub_row.addWidget(sub)
            sub_row.addStretch(1)
            v.addLayout(sub_row)
            v.addSpacing(10)
            row = QHBoxLayout()
            row.addStretch(1)
            b = button(t("btn_choose"), "get_lg", "add_files")
            b.clicked.connect(self.choose_file)
            b.setEnabled(not self._problem)
            row.addWidget(b)
            row.addSpacing(12)
            row.addWidget(label(t("drop_hint"), "drop"))
            row.addStretch(1)
            v.addLayout(row)
        else:
            self._file_in_chamber(v, self.pending[0])
            if len(self.pending) > 1:
                v.addSpacing(8)
                chips = QHBoxLayout()
                chips.setSpacing(4)
                chips.addStretch(1)
                chips.addWidget(label(t("more_pending", n=len(self.pending) - 1), "meta"))
                for it in self.pending[1:4]:
                    b = button(shown(it.path.name), "ghost", "file")
                    b.clicked.connect(lambda _=False, x=it: self.focus_pending(x))
                    chips.addWidget(b)
                chips.addStretch(1)
                v.addLayout(chips)
        v.addStretch(1)
        return ch

    def _file_in_chamber(self, v: QVBoxLayout, item: Pending) -> None:
        ic = QLabel()
        ic.setPixmap(glyph_pixmap("file", 56, "#f2f2f4"))
        v.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        name = label(shown(item.path.name), "file_big", wrap=True)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(name)
        where = label("", "path")
        full = shown(f"{item.path.parent}  ·  {item.mime or '…'}")
        where.setText(where.fontMetrics().elidedText(full, Qt.TextElideMode.ElideMiddle, 520))
        where.setToolTip(full)
        where.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(where)
        v.addSpacing(12)

        if not item.loaded:
            v.addWidget(Pill("…", "busy"), 0, Qt.AlignmentFlag.AlignHCenter)
            return
        usable = [h for h in item.handlers if h.usable]
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        if not usable:
            msg = label(t("no_handler"), "warnline", wrap=True)
            msg.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(msg)
        else:
            combo = QComboBox()
            for h in item.handlers:
                icon = QIcon.fromTheme(h.icon) if h.icon and "/" not in h.icon else QIcon()
                text = h.name if h.usable else f"{h.name} — {t('reason_' + h.reason)}"
                combo.addItem(icon, text, h)
                if not h.usable:
                    combo.model().item(combo.count() - 1).setEnabled(False)
            combo.setMinimumWidth(240)
            combo.setToolTip(t("open_with"))
            row.addWidget(combo)
            go = button(t("btn_open"), "get_lg", "box", icon_color="#000000")
            go.setEnabled(not self._problem)
            go.clicked.connect(lambda: self.open_pending(item, combo))
            row.addWidget(go)
        rm = button(t("btn_remove"), "ghost", "x")
        rm.clicked.connect(lambda: self.remove_pending(item))
        row.addWidget(rm)
        row.addStretch(1)
        v.addLayout(row)

    # What is in the box ────────────────────────────────────────────────────
    def _in_the_box(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(label(t("sec_running"), "section"))
        if not self.sessions:
            v.addWidget(label(t("running_empty"), "tagline"))
            return wrap
        flow = FlowLayout(spacing=10, min_item_width=260)
        for r in self.sessions:
            flow.addWidget(self._tile(r))
        holder = QWidget()
        holder.setLayout(flow)
        v.addWidget(holder)
        return wrap

    def _tile(self, r: session.Record) -> QWidget:
        tile = GlassFrame(radius=14, strength=1.2)
        h = QHBoxLayout(tile)
        h.setContentsMargins(14, 10, 8, 10)
        h.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(glyph_pixmap("box", 22, "#f2f2f4"))
        h.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        name = label("", "tile_name")
        fname = shown(os.path.basename(r.file))
        name.setText(name.fontMetrics().elidedText(fname, Qt.TextElideMode.ElideMiddle, 170))
        name.setToolTip(shown(r.file))
        col.addWidget(name)
        since = time.strftime("%H:%M", time.localtime(r.started))
        mode = t("mode_x11") if r.mode == "x11" else t("mode_wayland")
        col.addWidget(label(f"{r.app} · {mode}", "meta"))
        col.addWidget(label(t("since", time=since), "meta"))
        h.addLayout(col, 1)
        close = QPushButton(objectName="round")
        from mazeglovebox.gui.glyphs import glyph
        close.setIcon(glyph("x", 14, "#bdbdc2"))
        close.setFixedSize(32, 32)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(t("btn_close_box"))
        close.clicked.connect(lambda _=False, sid=r.id: self.close_session(sid))
        h.addWidget(close)
        return tile

    def _warn_box(self, text: str) -> QWidget:
        box = GlassFrame(radius=16, strength=1.3)
        h = QHBoxLayout(box)
        h.setContentsMargins(20, 14, 20, 14)
        ic = QLabel()
        ic.setPixmap(glyph_pixmap("warn", 18, "#f2f2f4"))
        h.addWidget(ic)
        h.addWidget(label(text, "warnline", wrap=True), 1)
        return box

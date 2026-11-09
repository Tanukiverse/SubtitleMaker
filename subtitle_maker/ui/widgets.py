"""Small widgets: a time box (hh:mm:ss.mmm), the video view with subtitles drawn on it, and the waveform."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QValidator
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QDoubleSpinBox, QGraphicsItem, QGraphicsScene, QGraphicsView, QWidget)

from ..core.project import Seg


def fmt_time(t: float) -> str:
    ms = max(0, round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


_TIME = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+)(?:[.,](\d{1,3}))?\s*$")


def parse_time(text: str) -> float | None:
    m = _TIME.match(text)
    if not m:
        return None
    a, b, sec, ms = m.groups()
    parts = [int(x) for x in (a, b) if x is not None]
    h, mi = ([0] * (2 - len(parts)) + parts) if parts else (0, 0)
    return h * 3600 + mi * 60 + int(sec) + (int(ms.ljust(3, "0")) / 1000 if ms else 0)


class TimeSpin(QDoubleSpinBox):
    """Seconds shown as hh:mm:ss.mmm. Arrows step 0.1 s; a typed value applies on Enter or when focus leaves."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(3)
        self.setRange(0, 360000)
        self.setSingleStep(0.1)
        self.setKeyboardTracking(False)
        self.setMinimumWidth(130)

    def textFromValue(self, v: float) -> str:
        return fmt_time(v)

    def valueFromText(self, text: str) -> float:
        v = parse_time(text)
        return self.value() if v is None else v

    def validate(self, text: str, pos: int):
        return (QValidator.Acceptable if parse_time(text) is not None else QValidator.Intermediate), text, pos


class SubtitleItem(QGraphicsItem):
    """The current cue, white with a black outline, centred near the bottom of the video."""

    def __init__(self):
        super().__init__()
        self.text = ""
        self.w, self.h = 1280.0, 720.0
        self.size_pct, self.bottom_pct = 6.0, 5.0
        self.color, self.outline = QColor("#ffffff"), QColor("#000000")
        self.setZValue(10)

    def set_style(self, size_pct: float, bottom_pct: float, color: str, outline: str) -> None:
        self.size_pct, self.bottom_pct = size_pct, bottom_pct
        self.color, self.outline = QColor(color), QColor(outline)
        self.update()

    def set_frame(self, w: float, h: float) -> None:
        self.prepareGeometryChange()
        self.w, self.h = w, h

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.w, self.h)

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        if not self.text:
            return
        font = QFont(); font.setPixelSize(max(12, int(self.h * self.size_pct / 100))); font.setBold(True)
        fm = QFontMetricsF(font)
        lines = self.text.split("\n")
        lh = fm.height() * 1.05
        y = self.h - self.h * self.bottom_pct / 100 - lh * len(lines) + fm.ascent()
        p.setRenderHint(QPainter.Antialiasing)
        for ln in lines:
            path = QPainterPath()
            path.addText(QPointF((self.w - fm.horizontalAdvance(ln)) / 2, y), font, ln)
            p.setPen(QPen(self.outline, max(2.0, self.h * 0.007), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.NoPen); p.setBrush(self.color)
            p.drawPath(path)
            y += lh


class VideoView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(0, 0, 0))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.video = QGraphicsVideoItem()
        self.video.setSize(QSizeF(1280, 720))
        self.scene().setSceneRect(0, 0, 1280, 720)
        self.sub = SubtitleItem()
        self.scene().addItem(self.video)
        self.scene().addItem(self.sub)
        self.video.nativeSizeChanged.connect(self._on_native_size)
        self.setMinimumHeight(180)

    def _on_native_size(self, size) -> None:
        if size.isEmpty():
            return
        self.video.setSize(size)
        self.sub.set_frame(size.width(), size.height())
        self.scene().setSceneRect(0, 0, size.width(), size.height())
        self.fit()

    def fit(self) -> None:
        self.fitInView(self.scene().sceneRect() if not self.scene().sceneRect().isEmpty() else self.video.boundingRect(),
                       Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()


class Waveform(QWidget):
    """Audio waveform with the cues drawn on it. Drag a cue's edge to retime it, drag its body to move it,
    click empty space to seek, Ctrl+wheel to zoom, wheel to scroll."""
    RATE = 100  # peak values per second
    RULER = 18
    EDGE = 5

    seekRequested = Signal(float)
    cueClicked = Signal(int)
    dragStarted = Signal(int)
    cueDragged = Signal(int, float, float)
    dragFinished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segs: list[Seg] = []
        self.mn = self.mx = None
        self.duration = 0.0
        self.t0 = 0.0
        self.spp = 0.03  # seconds per pixel
        self.playhead = 0.0
        self.selected = -1
        self.follow = True
        self.interactive = True
        self._drag = None
        self.setMinimumHeight(110)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

    # ---- data
    def load_wav(self, path: str | Path | None) -> None:
        self.mn = self.mx = None
        self.duration = 0.0
        if path and Path(path).exists():
            import wave
            with wave.open(str(path), "rb") as w:
                n, rate = w.getnframes(), w.getframerate()
                pcm = np.frombuffer(w.readframes(n), dtype=np.int16)
            step = max(1, rate // self.RATE)
            m = (len(pcm) // step) * step
            blocks = pcm[:m].reshape(-1, step)
            self.mn = blocks.min(axis=1).astype(np.float32) / 32768.0
            self.mx = blocks.max(axis=1).astype(np.float32) / 32768.0
            self.duration = n / rate
        self.t0 = 0.0
        self.update()

    def set_playhead(self, t: float) -> None:
        self.playhead = t
        span = self.width() * self.spp
        if self.follow and self._drag is None and (t < self.t0 or t > self.t0 + span * 0.95):
            self.t0 = max(0.0, t - span * 0.1)
        self.update()

    def set_selected(self, i: int) -> None:
        self.selected = i
        if 0 <= i < len(self.segs):
            s = self.segs[i]
            span = self.width() * self.spp
            if s.start < self.t0 or s.start > self.t0 + span * 0.9:
                self.t0 = max(0.0, s.start - span * 0.1)
        self.update()

    def zoom(self, factor: float, anchor_t: float | None = None) -> None:
        anchor_t = self.playhead if anchor_t is None else anchor_t
        x = (anchor_t - self.t0) / self.spp
        self.spp = min(max(self.spp / factor, 0.002), 2.0)
        self.t0 = max(0.0, anchor_t - x * self.spp)
        self.update()

    def zoom_all(self) -> None:
        if self.duration > 0 and self.width() > 0:
            self.spp = max(self.duration / self.width(), 0.002)
            self.t0 = 0.0
            self.update()

    # ---- geometry
    def _x(self, t: float) -> float:
        return (t - self.t0) / self.spp

    def _t(self, x: float) -> float:
        return self.t0 + x * self.spp

    def _hit(self, x: float):
        """(index, mode) for a cue under x; mode is 'start', 'end' or 'move'."""
        best = None
        for i, s in enumerate(self.segs):
            x1, x2 = self._x(s.start), self._x(s.end)
            if x < x1 - self.EDGE or x > x2 + self.EDGE:
                continue
            if abs(x - x1) <= self.EDGE and (best is None or best[1] != "start"):
                best = (i, "start")
            elif abs(x - x2) <= self.EDGE:
                best = (i, "end")
            elif x1 < x < x2 and best is None:
                best = (i, "move")
        return best

    # ---- painting
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(24, 24, 28))
        top = self.RULER
        mid = top + (h - top) / 2
        scale = (h - top) / 2 * 0.92

        # cues first so the waveform draws over them
        for i, s in enumerate(self.segs):
            x1, x2 = self._x(s.start), self._x(s.end)
            if x2 < 0 or x1 > w:
                continue
            sel = i == self.selected
            p.fillRect(QRectF(x1, top, max(x2 - x1, 1), h - top), QColor(255, 170, 0, 70) if sel else QColor(70, 130, 255, 50))
            p.setPen(QColor(255, 190, 60) if sel else QColor(110, 160, 255))
            p.drawLine(QPointF(x1, top), QPointF(x1, h)); p.drawLine(QPointF(x2, top), QPointF(x2, h))

        if self.mx is not None:
            p.setPen(QColor(120, 200, 140))
            n = len(self.mx)
            for x in range(w):
                i0 = int(self._t(x) * self.RATE)
                i1 = max(i0 + 1, int(self._t(x + 1) * self.RATE))
                if i1 <= 0 or i0 >= n:
                    continue
                i0, i1 = max(i0, 0), min(i1, n)
                p.drawLine(x, int(mid - float(self.mx[i0:i1].max()) * scale), x, int(mid - float(self.mn[i0:i1].min()) * scale) + 1)
        else:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, "ไม่มีคลื่นเสียง (ทำขั้น 1. ดึงเสียง ก่อน)")

        # labels on top of the waveform
        p.setFont(QFont(self.font().family(), 8))
        for i, s in enumerate(self.segs):
            x1, x2 = self._x(s.start), self._x(s.end)
            if x2 < 0 or x1 > w or x2 - x1 < 14:
                continue
            p.setPen(QColor(255, 255, 255, 230))
            p.drawText(QRectF(x1 + 2, top, x2 - x1 - 4, 28), Qt.AlignLeft | Qt.AlignTop,
                       f"{i + 1}  {(s.th or s.text).replace(chr(10), ' ')}")

        # ruler
        p.fillRect(0, 0, w, top, QColor(40, 40, 46))
        step = next((s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600) if s / self.spp >= 70), 1200)
        t = int(self.t0 / step) * step
        p.setPen(QColor(190, 190, 190))
        p.setFont(QFont(self.font().family(), 8))
        while self._x(t) < w:
            x = self._x(t)
            p.drawLine(QPointF(x, top - 5), QPointF(x, top))
            if x >= 0:
                p.drawText(QPointF(x + 3, top - 5), fmt_time(t)[:-4] if step >= 1 else fmt_time(t)[3:-1])
            t += step

        px = self._x(self.playhead)
        p.setPen(QPen(QColor(255, 70, 70), 1.5))
        p.drawLine(QPointF(px, 0), QPointF(px, h))

    # ---- mouse
    def mousePressEvent(self, e) -> None:
        if not self.interactive or e.button() != Qt.LeftButton:
            return
        x = e.position().x()
        hit = self._hit(x)
        t = self._t(x)
        if hit is None:
            self.seekRequested.emit(t)
            return
        i, mode = hit
        s = self.segs[i]
        self.selected = i
        self.cueClicked.emit(i)
        self._drag = {"i": i, "mode": mode, "t": t, "start": s.start, "end": s.end, "moved": False, "seg": s}
        self.update()

    def mouseMoveEvent(self, e) -> None:
        x = e.position().x()
        d = self._drag
        if d is None:
            hit = self._hit(x) if self.interactive else None
            self.setCursor(Qt.SizeHorCursor if hit and hit[1] != "move" else Qt.ArrowCursor)
            return
        dt = self._t(x) - d["t"]
        if not d["moved"]:
            if abs(dt) / self.spp < 3:
                return
            d["moved"] = True
            self.dragStarted.emit(d["i"])
        start, end = d["start"], d["end"]
        if d["mode"] == "start":
            start = min(max(0.0, start + dt), end - 0.1)
        elif d["mode"] == "end":
            end = max(end + dt, start + 0.1)
        else:
            dt = max(dt, -start)
            start, end = start + dt, end + dt
        self.cueDragged.emit(d["i"], start, end)
        # the window may re-sort the list: find our cue again
        try:
            d["i"] = next(k for k, s in enumerate(self.segs) if s is d["seg"])
        except StopIteration:
            pass
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        d, self._drag = self._drag, None
        if d is None:
            return
        if d["moved"]:
            self.dragFinished.emit(d["i"])
        else:
            self.seekRequested.emit(self._t(e.position().x()))

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.ControlModifier:
            self.zoom(1.25 if e.angleDelta().y() > 0 else 0.8, self._t(e.position().x()))
        else:
            self.t0 = max(0.0, self.t0 - (1 if e.angleDelta().y() > 0 else -1) * self.width() * self.spp * 0.15)
            self.update()

"""A small animated companion for the panel, drawn from skin/mascot_bust.png.

It sits in a strip above the tabs, with a speech bubble to its right, and acts
out what the app is doing: idle, listening (a soft halo and sound bars),
thinking (dots), answering (a hop and sparkles) and error (a shake). Clicking
it gets a word of encouragement. Only built when the skin exists (skin.py).
"""
import math
import random
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QImageReader, QPainter, QPainterPath, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from skin import SKIN_DIR

BUST_FILE = SKIN_DIR / "mascot_bust.png"
FRAME_MS = 33          # ~30 fps is plenty for a gentle bob, and cheap next to whisper
BUBBLE = QColor("#252332")
BUBBLE_MAX_WIDTH = 240


class MascotWidget(QWidget):
    clicked = Signal()
    STATES = ("idle", "listening", "thinking", "answering", "error")
    LINES = ("¡Tú puedes!", "¡Respira, lo estás haciendo genial!", "¿Te ayudo con algo?",
             "¡Vamos paso a paso!", "¡Estoy aquí contigo!", "Una idea a la vez")

    @staticmethod
    def available():
        """Whether the artwork exists (no QApplication needed, never raises)."""
        return QImageReader(str(BUST_FILE)).canRead()

    def __init__(self, skin, height=104, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Haz clic para que te dé ánimo")
        self.skin = skin
        self.accent = QColor(skin.accent)
        self.state = "idle"
        self._height = height
        self._source = QPixmap(str(BUST_FILE))
        self._scaled = QPixmap()          # the bust pre-scaled for this size and DPI
        self._hover = False
        self._hover_amount = 0.0
        self._text = ""
        self._say_until = 0.0
        self._clock = time.monotonic()
        self._phase = 0.0
        self._burst = 1.0
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(850)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.OutBack)
        self._animation.valueChanged.connect(self._animate)
        self.setFixedHeight(height + 14)  # room for the hop

    def sizeHint(self):
        return QSize(300, self._height + 14)

    def set_state(self, name):
        """Switch the pose; unknown states fall back to idle."""
        name = name if name in self.STATES else "idle"
        if name == self.state:
            return
        self.state = name
        self._clock = time.monotonic()
        self._phase = 0.0
        if name in ("answering", "error") and self.isVisible():
            self._animation.start()
        self.update()

    def say(self, text, ms=2500):
        """Show a short message in the bubble for `ms` milliseconds."""
        self._text = str(text)[:100]
        self._say_until = time.monotonic() + max(0, ms)/1000
        self.update()

    def _animate(self, value):
        self._burst = float(value)
        self.update()

    def _tick(self):
        self._phase = time.monotonic() - self._clock
        self._hover_amount += (float(self._hover) - self._hover_amount)*.18
        if self._text and time.monotonic() >= self._say_until:
            self._text = ""
        self.update()

    def showEvent(self, event):
        self._clock = time.monotonic() - self._phase
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()               # no repaints while the panel is hidden
        self._animation.stop()
        super().hideEvent(event)

    def enterEvent(self, event):
        self._hover = True
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._animation.start()
            self.say(random.choice(self.LINES))
            self.clicked.emit()
            event.accept()               # don't start a panel drag from here
            return
        super().mousePressEvent(event)

    def _bust(self, dpr):
        """The bust scaled once per size/DPI (with headroom for the hover zoom),
        instead of resampling the full-size art on every frame."""
        target = round(self._height*1.04*dpr)
        if self._scaled.isNull() or self._scaled.height() != target:
            self._scaled = self._source.scaledToHeight(target, Qt.SmoothTransformation)
        return self._scaled

    def paintEvent(self, event):
        if self._source.isNull():
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        t = self._phase
        bust = self._bust(self.devicePixelRatioF())
        width = self._height*bust.width()/bust.height()
        cx = 10 + width/2
        bottom = self.height()
        cy = bottom - self._height/2
        pulse = .5 + .5*math.sin(t*4)

        if self.state == "listening":
            glow = QRadialGradient(QPointF(cx, cy), self._height*.62)
            color = QColor(self.accent)
            color.setAlpha(round(40 + 40*pulse))
            glow.setColorAt(0, color)
            glow.setColorAt(1, Qt.transparent)
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(cx, cy), self._height*.62, self._height*.62)

        bob = 1.5*math.sin(t*2)
        tilt = -2*self._hover_amount
        if self.state == "thinking":
            tilt += 2.5*math.sin(t*1.8)
        shake = 0.0
        if self.state == "error" and t < .65:
            shake = 5*math.sin(t*48)*(1 - t/.65)
        # A decaying hop when an answer lands; OutBack gives clicks an overshoot.
        progress = min(1, t/1.1)
        hop = 8*abs(math.sin(progress*math.pi*2))*(1 - progress) if self.state == "answering" else 0
        if self._animation.state() == QVariantAnimation.Running:
            hop += 6*math.sin(min(1, max(0, self._burst))*math.pi)
        p.save()
        p.translate(cx + shake, bottom + bob - hop)
        p.rotate(tilt)
        scale = 1 + .006*math.sin(t*2) + .03*self._hover_amount
        p.scale(scale, scale)
        p.drawPixmap(QRectF(-width/2, -self._height, width, self._height), bust,
                     QRectF(bust.rect()))
        p.restore()

        if self.state == "answering" and t < 1.4:
            progress = t/1.4
            colors = [self.accent, self.skin.highlight, self.skin.secondary]
            for i in range(9):
                angle = i*2.4
                radius = 30 + progress*48
                x, y = cx + math.cos(angle)*radius, cy + math.sin(angle)*radius*.7
                color = QColor(colors[i % 3])
                color.setAlphaF(max(0, 1 - progress))
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                size = 3*(1 - progress) + 1
                star = QPainterPath()
                star.moveTo(x, y - size*2)
                for dx, dy in ((.6, -.6), (2, 0), (.6, .6), (0, 2), (-.6, .6), (-2, 0), (-.6, -.6)):
                    star.lineTo(x + dx*size, y + dy*size)
                star.closeSubpath()
                p.drawPath(star)

        self._paint_bubble(p, cx + width/2 + 16, t)

    def _paint_bubble(self, p, left, t):
        """Speech bubble to the right: a message, else dots while thinking or
        sound bars while listening."""
        message = self._text
        mode = "text" if message else {"thinking": "dots", "listening": "bars"}.get(self.state)
        if not mode:
            return
        right = self.width() - 4
        if right - left < 80:
            return
        h = 58 if mode == "text" else 34
        width = min(right - left, BUBBLE_MAX_WIDTH) if mode == "text" else 74
        box = QRectF(left, (self.height() - h)/2, width, h)
        edge = QColor(self.accent)
        edge.setAlpha(110)
        p.setPen(edge)
        p.setBrush(BUBBLE)
        p.drawRoundedRect(box, 12, 12)
        tail = QPainterPath()
        mid = box.center().y()
        tail.moveTo(box.left() + 1, mid - 6)
        tail.lineTo(box.left() - 8, mid + 2)
        tail.lineTo(box.left() + 1, mid + 6)
        p.fillPath(tail, BUBBLE)
        p.setPen(Qt.NoPen)
        p.setBrush(self.accent)
        if mode == "dots":
            for i in range(3):
                p.drawEllipse(QPointF(box.center().x() - 16 + i*16,
                                      mid + 3*math.sin(t*6 - i)), 3, 3)
        elif mode == "bars":
            for i in range(5):
                bar = 4 + 14*(.5 + .5*math.sin(t*7 + i*.9))
                p.drawRoundedRect(QRectF(box.center().x() - 20 + i*9, mid - bar/2, 4, bar), 2, 2)
        else:
            p.setPen(QColor("#ECEEF2"))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(box.adjusted(10, 6, -10, -6), Qt.AlignCenter | Qt.TextWordWrap, message)

"""Optional personal skin: a palette and artwork cut from your own wallpaper by
scripts/make_skin.py. Everything lives in skin/ (gitignored, since wallpaper
art usually belongs to someone else). Without it the panel keeps its default
coral look, so this module is safe to import when skin/ doesn't exist.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap

SKIN_DIR = Path(__file__).resolve().parents[1] / "skin"
PANEL_RADIUS = 16
# The default theme's coral, as written in overlay.py's stylesheet.
_CORAL, _CORAL_HOVER, _CORAL_SOFT, _CORAL_RGB = "#F08A5D", "#F49C75", "#FFC9AE", "240,138,93"


@dataclass
class Skin:
    accent: QColor
    accent_soft: QColor
    highlight: QColor
    secondary: QColor
    panel_tint: QColor
    directory: Path = SKIN_DIR
    _background: QPixmap = field(default=None, repr=False)


def load_skin():
    """Return a validated palette, or None when the optional skin is unavailable."""
    try:
        values = json.loads((SKIN_DIR / "palette.json").read_text(encoding="utf-8"))
        colors = {key: QColor(values[key]) for key in
                  ("accent", "accent_soft", "highlight", "secondary", "panel_tint")}
        if not all(c.isValid() for c in colors.values()):
            return None
        # Accents are used as text on the dark panel, so keep them light enough.
        for key in ("accent", "accent_soft"):
            c = colors[key]
            while _luminance(c) < .34:
                c = c.lighter(110) if c.value() else QColor("#ED85BD")
            colors[key] = c
        return Skin(**colors)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _luminance(color):
    values = color.getRgbF()[:3]
    return sum((v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4)*w
               for v, w in zip(values, (.2126, .7152, .0722)))


def themed(qss, skin):
    """The overlay stylesheet with the default coral swapped for the skin's
    accent, so every accented widget follows the skin, new ones included."""
    if skin is None:
        return qss
    r, g, b, _ = skin.accent.getRgb()
    return (qss.replace(_CORAL_HOVER, skin.accent.lighter(112).name())
               .replace(_CORAL_SOFT, skin.accent_soft.name())
               .replace(_CORAL, skin.accent.name())
               .replace(_CORAL_RGB, f"{r},{g},{b}"))


def paint_panel_background(painter, rect, skin):
    """Paint the panel tinted with the skin, with a heavily softened watermark of
    the character in the lower right."""
    rect = QRectF(rect)
    painter.save()
    painter.setRenderHint(QPainter.SmoothPixmapTransform)   # a soft blur, not blocks
    path = QPainterPath()
    path.addRoundedRect(rect.adjusted(.5, .5, -.5, -.5), PANEL_RADIUS, PANEL_RADIUS)
    painter.setClipPath(path)
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    top = QColor("#161922")
    tint = skin.panel_tint
    top = QColor(round(top.red()*.88+tint.red()*.12),
                 round(top.green()*.88+tint.green()*.12), round(top.blue()*.88+tint.blue()*.12))
    grad.setColorAt(0, top)
    grad.setColorAt(1, QColor("#0B0D12"))
    painter.fillRect(rect, grad)
    if skin._background is None:
        # Shrunk to 65 px and scaled back up, it blurs into a soft shape that
        # never competes with the text on top.
        source = QPixmap(str(skin.directory / "mascot.png"))
        skin._background = (source.scaledToHeight(65, Qt.SmoothTransformation)
                            if not source.isNull() else QPixmap())
    if not skin._background.isNull():
        height = rect.height()*.82
        width = height*skin._background.width()/skin._background.height()
        painter.setOpacity(.075)
        painter.drawPixmap(QRectF(rect.right()-width+25, rect.bottom()-height+20, width, height),
                           skin._background, QRectF(skin._background.rect()))
        painter.setOpacity(1)
    painter.setPen(QColor(255, 255, 255, 23))
    painter.drawPath(path)
    painter.restore()

"""Render the README screenshots into assets/screenshots/.

The overlay is excluded from screen capture, so a normal screenshot shows nothing.
Instead this renders the real Overlay widget straight to a pixmap with Qt
(QWidget.grab) filled with made-up demo content, then composites it over a drawn
video-call scene. No audio, no Claude calls, no real meeting data.

    .venv\\Scripts\\python.exe scripts\\screenshots.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_SCALE_FACTOR", "2")   # crisp 2x renders

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = ROOT / "assets" / "screenshots"
SCALE = 2
W, H = 1280, 720

DEMO_TRANSCRIPT = [
    ("Them", "Gracias por venir. Les cuento: tenemos 12 tiendas y queremos que los "
             "clientes vuelvan más seguido."),
    ("You", "Perfecto, para eso justamente está pensado el programa."),
    ("Them", "¿Cuánto tiempo les tomaría tenerlo funcionando en todas las tiendas?"),
    ("Them", "Y otra cosa, ¿de quién son los datos de nuestros clientes?"),
    ("You", "Buena pregunta, déjame explicarte cómo lo manejamos."),
]
DEMO_CONVO = [
    ("auto", "Lo hacemos en dos fases: en **3 semanas** dejamos funcionando 2 tiendas "
             "piloto y en 6 semanas más escalamos a las 12, con el panel de datos incluido."),
    ("auto", "Los datos son **de ustedes**: quedan en su cuenta, los pueden exportar "
             "cuando quieran y firmamos un acuerdo de tratamiento de datos."),
    ("you", "¿Qué le digo si me pide descuento por pagar anual?"),
    ("claude", "Ofrece valor en vez de bajar el precio:\n\n"
               "- **2 meses gratis** si firma anual (≈16 %)\n"
               "- Mantén el precio mensual como referencia\n"
               "- Pide a cambio un caso de éxito publicable"),
]
PARTICIPANTS = [
    ("Carolina Ruiz", "Gerenta comercial", "#3B4A6B", "#5B6F9A"),
    ("Martín Soto", "Jefe de tiendas", "#4A3B5E", "#7A5E96"),
    ("Daniela Paz", "Marketing", "#2F5149", "#4E8577"),
    ("Tú", "", "#4D3A30", "#8A6450"),
]


def make_overlay(app, state):
    from overlay import Overlay

    ov = Overlay()
    ov.resize(420, 640)
    if state in ("answers", "transcript"):
        ov._live = True
        ov._set_power(True)
        for src, text in DEMO_TRANSCRIPT:
            ov._on_transcript(text, src)
        ov._convo = list(DEMO_CONVO)
        ov._render_chat()
        ov.notes.setPlainText("Cliente: cadena de 12 tiendas. Plan Pro mensual.")
    ov.tabs.setCurrentIndex(1 if state == "transcript" else 0)
    ov.show()
    for _ in range(5):
        app.processEvents()
    if ov._live:  # after show(): showEvent sets the capture-exclusion status
        ov._set_status("escuchando (tú + llamada)")
        app.processEvents()
    pix = ov.grab()
    ov.hide()
    ov.deleteLater()
    return with_round_corners(pix)


def with_round_corners(pix, radius=16):
    """The live window gets its rounded corners from a mask, which grab() ignores;
    clip them here (antialiased) so the PNG has transparent corners."""
    dpr = pix.devicePixelRatio()
    out = QPixmap(pix.size())
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, pix.width() / dpr, pix.height() / dpr), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


def rounded(p, rect, radius, fill, pen=Qt.NoPen):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.setPen(pen)
    p.fillPath(path, fill)
    if pen != Qt.NoPen:
        p.drawPath(path)


def soft_shadow(p, rect, radius, strength=60, spread=26):
    """Fake a blurred drop shadow with stacked translucent rounded rects."""
    for i in range(spread, 0, -2):
        a = int(strength * (1 - i / spread) ** 2 / 6) + 1
        r = rect.adjusted(-i, -i + 10, i, i + 10)
        rounded(p, r, radius + i, QColor(0, 0, 0, a))


def draw_call(p, sharing_badge):
    """A generic dark video-call window filling the canvas."""
    p.fillRect(QRectF(0, 0, W, H), QColor("#0B0D12"))
    # title bar
    p.setPen(QColor("#9AA3B5"))
    p.setFont(QFont("Segoe UI", 10))
    p.drawText(QRectF(24, 0, 700, 44), Qt.AlignVCenter,
               "Reunión con cliente  ·  Programa de fidelización")
    p.setPen(QColor("#6B7385"))
    p.drawText(QRectF(W - 224, 0, 200, 44), Qt.AlignVCenter | Qt.AlignRight, "24:13")
    if sharing_badge:
        badge = QRectF(W / 2 - 120, 9, 240, 26)
        rounded(p, badge, 13, QColor(52, 211, 153, 40))
        p.setPen(QColor("#A7F3D0"))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.drawText(badge, Qt.AlignCenter, "●  Estás compartiendo tu pantalla")
    # participant grid
    gx, gy, gap = 24, 52, 12
    tw = (W - 2 * gx - gap) / 2
    th = (H - gy - 92 - gap) / 2
    for i, (name, role, c1, c2) in enumerate(PARTICIPANTS):
        x = gx + (i % 2) * (tw + gap)
        y = gy + (i // 2) * (th + gap)
        tile = QRectF(x, y, tw, th)
        grad = QLinearGradient(tile.topLeft(), tile.bottomRight())
        grad.setColorAt(0, QColor(c1))
        grad.setColorAt(1, QColor("#12151C"))
        rounded(p, tile, 14, grad)
        cx, cy, r = tile.center().x(), tile.center().y() - 6, 46
        p.setBrush(QColor(c2))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)
        initials = "".join(w[0] for w in name.split()[:2]).upper()
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont("Segoe UI", 22, QFont.DemiBold))
        p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, initials)
        label = QRectF(x + 12, y + th - 40, 260, 28)
        rounded(p, label, 8, QColor(0, 0, 0, 110))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.setPen(QColor("#ECEEF2"))
        p.drawText(label.adjusted(10, 0, -8, 0), Qt.AlignVCenter,
                   name + (f"  ·  {role}" if role else ""))
    # control bar
    bar_y = H - 66
    specs = [("#2A2F3A", "🎙"), ("#2A2F3A", "📷"), ("#1F5F4A", "🖥"), ("#C2413A", "✕")]
    total = len(specs) * 48 + (len(specs) - 1) * 14
    x0 = (W - total) / 2
    p.setFont(QFont("Segoe UI Emoji", 14))
    for i, (col, glyph) in enumerate(specs):
        btn = QRectF(x0 + i * 62, bar_y, 48, 48)
        p.setBrush(QColor(col))
        p.setPen(Qt.NoPen)
        p.drawEllipse(btn)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(btn, Qt.AlignCenter, glyph)


def canvas(w, h, color=None):
    img = QImage(int(w * SCALE), int(h * SCALE), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(SCALE)
    img.fill(QColor(color) if color else Qt.transparent)
    return img


def scene(panel, with_panel, sharing_badge=False):
    img = canvas(W, H)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    draw_call(p, sharing_badge)
    if with_panel:
        pw = panel.width() / panel.devicePixelRatio()
        ph = panel.height() / panel.devicePixelRatio()
        k = (H - 70) / ph
        rect = QRectF(W - pw * k - 28, 38, pw * k, ph * k)
        soft_shadow(p, rect, 16)
        p.drawPixmap(rect, panel, QRectF(panel.rect()))
    p.end()
    return img


def side_by_side(left, right):
    k, pad, head = 0.5, 28, 46
    cw, ch = W * k, H * k
    img = canvas(2 * cw + 3 * pad, ch + head + pad, "#0E1117")
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    for i, (shot, title, color) in enumerate((
            (left, "Lo que ves tú", "#F08A5D"),
            (right, "Lo que ven los demás cuando compartes pantalla", "#34D399"))):
        x = pad + i * (cw + pad)
        p.setPen(QColor(color))
        p.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        p.drawText(QRectF(x, 0, cw, head), Qt.AlignVCenter, title)
        target = QRectF(x, head, cw, ch)
        path = QPainterPath()
        path.addRoundedRect(target, 10, 10)
        p.save()
        p.setClipPath(path)
        p.drawImage(target, shot, QRectF(shot.rect()))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawPath(path)
    p.end()
    return img


def panels_row(pixmaps):
    pad = 36
    widths = [px.width() / px.devicePixelRatio() for px in pixmaps]
    ph = max(px.height() / px.devicePixelRatio() for px in pixmaps)
    img = canvas(sum(widths) + pad * (len(pixmaps) + 1), ph + 2 * pad)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    x = pad
    for px, w in zip(pixmaps, widths):
        rect = QRectF(x, pad, w, px.height() / px.devicePixelRatio())
        soft_shadow(p, rect, 16, strength=45, spread=20)
        p.drawPixmap(rect, px, QRectF(px.rect()))
        x += w + pad
    p.end()
    return img


def main():
    app = QApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    answers = make_overlay(app, "answers")
    transcript = make_overlay(app, "transcript")
    idle = make_overlay(app, "idle")

    hero = scene(answers, with_panel=True)
    hero.save(str(OUT / "hero.png"))
    shared = scene(answers, with_panel=False, sharing_badge=True)
    side_by_side(scene(answers, with_panel=True, sharing_badge=True), shared) \
        .save(str(OUT / "invisible.png"))
    panels_row([idle, answers, transcript]).save(str(OUT / "panels.png"))
    for name, px in (("panel-answers", answers), ("panel-transcript", transcript),
                     ("panel-idle", idle)):
        px.save(str(OUT / f"{name}.png"))
    print(f"wrote {sorted(f.name for f in OUT.glob('*.png'))}")


if __name__ == "__main__":
    main()

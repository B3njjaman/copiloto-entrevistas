"""Copiloto overlay: a frameless, always-on-top panel excluded from screen
capture (SetWindowDisplayAffinity). Live mode continuously listens to the call,
transcribes locally, and auto-answers questions aimed at you.
"""
import base64
import ctypes
import html
import time
from difflib import SequenceMatcher

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QRectF, Qt, QTimer, QUrl, Signal, QThread
from PySide6.QtGui import (
    QColor, QDesktopServices, QImage, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QRegion,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizeGrip, QTabWidget, QTextBrowser,
    QTextEdit, QVBoxLayout, QWidget,
)

import brain
from config import (
    APP_NAME, AUTO_FROM, AUTO_MIN_NEWCHARS, AUTO_THROTTLE_S, ECHO_GUARD,
    ECHO_RATIO, ECHO_WINDOW_S, MY_NAME, NOTES_DIR, OPACITY, VISION_MONITOR,
)
from ears import Listener, list_output_devices

# Win32 constants
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

user32 = ctypes.windll.user32
user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.SetWindowDisplayAffinity.restype = ctypes.c_bool
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.restype = ctypes.c_long

# --- palette (the stylesheet below uses the same values) ---
ACCENT = "#F08A5D"        # coral: suggestions, focus, brand dot
THEM_COLOR = "#7CC4FA"    # "Ellos" in the transcript
YOU_COLOR = "#A3ACBE"     # "Tú" in the transcript
SOURCE_LABELS = {"Them": "Ellos", "You": "Tú"}
PANEL_RADIUS = 16

AUTO_PROMPT = (
    "In this live meeting I may have just been asked something. From the recent "
    "transcript, give me a crisp answer I can say out loud right now (1-3 sentences), "
    "in the language the meeting is in. Reply with only the words I'll say, in first "
    "person: no quotes, no 'they're asking…', no 'you could say'. If no question was actually "
    "directed at me, instead give a single short bullet noting the key point just "
    "discussed."
)
QUESTION_HINTS = (
    "?", "what do you", "what's your", "your thoughts", "thoughts on", "how would you",
    "can you", "could you", "do you have", "any questions", "over to you",
    "what about you", "your take", "tell me", "tell us", "walk me", "walk us",
    "explain", "describe", "give us", "give me", "how do", "how does", "why ",
    # Spanish. Whisper usually writes the opening "¿", which is the strongest hint.
    "¿", "qué opinas", "qué piensas", "qué te parece", "qué les parece",
    "qué opina", "qué propones", "qué proponen", "cómo lo harías", "cómo lo harían",
    "me puedes", "nos puedes", "me podrías", "nos podrías", "nos puede", "me puede",
    "cuéntame", "cuéntanos", "cuénteme", "cuéntenos", "explícame", "explícanos",
    "explíqueme", "explíquenos", "alguna pregunta", "tu opinión", "su opinión",
    "cuánto cuesta", "cuánto sale", "cuánto demora", "cuánto tiempo", "por qué",
    # whisper `small` sometimes drops the "¿" and the accents of short questions
    "qué pasa", "que pasa con", "de quién", "de quien son", "quién es", "cuál es",
    "cómo funciona", "cómo se", "por que no",
)
# If you set CLUELY_MY_NAME, treat a line that says your name as aimed at you
# ("Ada, what do you think?"). Each name word is matched, lowercased.
if MY_NAME:
    QUESTION_HINTS = QUESTION_HINTS + tuple(
        w for w in MY_NAME.lower().split() if len(w) >= 2
    )
# Whisper often drops the "?", so also treat an utterance that OPENS with a
# question/imperative word as a question — catches "tell me about…", "walk us
# through…", "how are you handling…" that match none of the fixed phrases above.
QUESTION_WORDS = frozenset((
    "what", "how", "why", "when", "where", "who", "which", "whose", "can", "could",
    "would", "will", "should", "do", "does", "did", "are", "is", "was", "were",
    "have", "has", "tell", "walk", "explain", "describe", "give", "talk",
    "qué", "cómo", "cuál", "cuáles", "cuándo", "dónde", "quién", "quiénes",
    "cuánto", "cuánta", "cuántos", "cuántas", "puedes", "podrías", "pueden",
    "podrían", "tienes", "tienen", "dime", "dinos", "dígame", "díganos",
    "cuéntame", "cuéntanos", "explícame", "explícanos", "háblame", "háblanos",
    "describa",
))
# Spoken questions often open with a connector ("Y ¿qué pasa…?", "So how…"), so
# these are skipped before checking the first word.
LEADING_FILLERS = frozenset((
    "y", "e", "pero", "entonces", "bueno", "ok", "okay", "ya", "perfecto", "oye",
    "and", "so", "but", "well",
))
VISION_PROMPT = (
    "This image is my current screen. If it shows a coding problem (e.g. LeetCode), "
    "respond with: "
    "(1) 'Problem (say this first):' — restate the problem in my own plain words as 1-2 "
    "natural first-person sentences I can say out loud to show I understood what's being "
    "asked: what I'm given (inputs), what I have to return (output), and the key "
    "constraint or edge case. "
    "(2) the optimal approach in one line, (3) time and space complexity, "
    "(4) a complete, correct, runnable solution in the language shown (default Python), "
    "with a line number at the start of every line, "
    "(5) a 'Read aloud (line by line):' section keyed to those line numbers "
    "('Line 1: ...', 'Line 2: ...', covering every line) — one short, natural spoken "
    "sentence per line in a relaxed, conversational first-person voice (contractions, "
    "plain words, not robotic) that I can read out verbatim. "
    "If it is not a coding problem, concisely explain what is on screen and what I should "
    "say or do next. Keep it tight and glanceable."
)
EMPTY_FEED = (
    "**Todo listo.**\n\n"
    "Pulsa **● Escuchar** y, cuando te pregunten algo en la llamada, aquí aparece "
    "qué decir.\n\n"
    "También puedes escribirle a Claude abajo o usar **Leer pantalla**."
)
HOTKEY_HINT = "Ctrl+Alt+Space escuchar · Ctrl+Alt+H ocultar · Ctrl+Alt+S pantalla"
# role -> (card objectName, header shown above the message)
CARD_KINDS = {
    "auto": ("cardAuto", "DI ESTO"),
    "claude": ("cardClaude", "CLAUDE"),
    "you": ("cardYou", "TÚ"),
}


def _qimage_to_b64(img) -> str:
    """Encode a QImage as a base64 PNG string."""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(ba.data())).decode("ascii")


def _open_web_link(url: str):
    """Open a link from a card only if it is a web URL. Model text can be steered
    by what is said on the call, so file:, ms-*: and custom schemes are ignored."""
    if url.lower().startswith(("https://", "http://")):
        QDesktopServices.openUrl(QUrl(url))


def _norm_line(text: str) -> str:
    """Lowercase + collapse whitespace so echo comparison ignores casing and the
    minor spacing differences two whisper passes produce on the same utterance."""
    return " ".join(text.lower().split())


class AskWorker(QThread):
    """Runs one query off the UI thread, streaming text back via signals."""
    chunk = Signal(str)
    finished_text = Signal(str)
    failed = Signal(str)

    def __init__(self, question, transcript, notes, image_b64="", fast=False):
        super().__init__()
        self._q, self._t, self._n = question, transcript, notes
        self._img = image_b64
        self._fast = fast

    def run(self):
        try:
            full = brain.ask(self._q, self._t, self._n,
                             on_text=lambda s: self.chunk.emit(s),
                             image_b64=self._img, fast=self._fast)
            self.finished_text.emit(full)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class MessageCard(QFrame):
    """One message in the Answers feed: a small caps header over a Markdown body.
    The body is re-set on every streamed chunk, so it skips unchanged text."""

    def __init__(self, role):
        super().__init__()
        name, header = CARD_KINDS.get(role, CARD_KINDS["claude"])
        self.setObjectName(name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 10)
        lay.setSpacing(4)
        self.header = QLabel(header)
        self.header.setObjectName("cardHeader")
        self.body = QLabel()
        self.body.setObjectName("cardBody")
        self.body.setWordWrap(True)
        # Your own typed text is shown as-is; Claude's answers render as Markdown.
        self.body.setTextFormat(Qt.PlainText if role == "you" else Qt.MarkdownText)
        self.body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.body.linkActivated.connect(_open_web_link)
        lay.addWidget(self.header)
        lay.addWidget(self.body)
        self._text = None

    def set_text(self, text):
        if text == self._text:
            return
        self._text = text
        self.body.setText(text or "_pensando…_")


class Overlay(QWidget):
    sig_toggle = Signal()
    sig_focus_input = Signal()
    sig_toggle_clickthrough = Signal()
    sig_live = Signal()
    sig_vision = Signal()
    sig_image = Signal()
    sig_quit = Signal()

    def __init__(self):
        super().__init__()
        self._click_through = False
        self._drag_pos = None
        self._workers = []
        self._convo = []                 # list[ (role, text) ]
        self._cards = []                 # MessageCard per _convo entry
        self._transcript_buf = []
        self._live = False
        self._listener = None
        self._retiring = []              # listeners stopped but not yet finished
        self._last_auto = 0.0
        self._last_auto_len = 0
        self._auto_inflight = False
        self._pending_auto = None        # newest follow-up queued while answering
        self._recent_lines = []          # [(monotonic_ts, source, norm_text)] echo guard
        # Streamed chunks are coalesced into one re-render every 60 ms, not one per token.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._render_chat)

        # No WA_TranslucentBackground: on Windows 10 SetWindowDisplayAffinity fails
        # (error 8) on per-pixel-alpha windows, which left the panel VISIBLE in screen
        # shares. Rounded corners come from a window mask instead (resizeEvent).
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowOpacity(OPACITY)
        self.resize(420, 620)

        self._build_ui()
        self._apply_style()
        self._set_power(False)
        self._position_top_right()

        self.sig_toggle.connect(self.toggle_visible)
        self.sig_focus_input.connect(self.focus_input)
        self.sig_toggle_clickthrough.connect(self.toggle_clickthrough)
        self.sig_live.connect(self.toggle_live)
        self.sig_vision.connect(self.ask_screen)
        self.sig_image.connect(self.attach_image)
        self.sig_quit.connect(self._quit)
        self.input.installEventFilter(self)

    # ---------- construction ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 8)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.title = QLabel(
            f'<span style="color:{ACCENT}">●</span>&nbsp; {APP_NAME}'
            f'<span style="color:#5C6476; font-weight:400">&nbsp; · Claude</span>')
        self.title.setObjectName("title")
        self.status = QLabel("iniciando…")
        self.status.setObjectName("status")
        self.status.setMaximumWidth(210)
        self.btn_close = QPushButton("×")
        self.btn_close.setObjectName("close")
        self.btn_close.setFixedSize(24, 22)
        self.btn_close.setToolTip("Cerrar (guarda tus notas)")
        self.btn_close.clicked.connect(self._quit)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.status)
        header.addWidget(self.btn_close)
        root.addLayout(header)

        # live controls row
        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        self.btn_live = QPushButton()
        self.btn_live.setObjectName("power")
        self.btn_live.setProperty("on", False)
        self.btn_live.setToolTip("Escuchar la llamada y sugerir respuestas  (Ctrl+Alt+Space)")
        self.btn_live.clicked.connect(self.toggle_live)
        self.btn_vision = QPushButton("Leer pantalla")
        self.btn_vision.setToolTip("Captura tu pantalla y Claude te dice qué responder  (Ctrl+Alt+S)")
        self.btn_vision.clicked.connect(self.ask_screen)
        # Checkable chip; isChecked()/setChecked() behave like the old checkbox.
        self.chk_auto = QPushButton("Auto-respuesta")
        self.chk_auto.setObjectName("chip")
        self.chk_auto.setCheckable(True)
        self.chk_auto.setChecked(True)
        self.chk_auto.setToolTip("Responder solo cuando detecto una pregunta para ti")
        ctl.addWidget(self.btn_live)
        ctl.addWidget(self.btn_vision)
        ctl.addStretch()
        ctl.addWidget(self.chk_auto)
        root.addLayout(ctl)

        # Which speaker output Cluely loopbacks for the *call* audio. WASAPI
        # loopback is endpoint-specific, so this MUST match Zoom's own Speaker
        # setting or the other side is captured as pure silence (no "Them" lines,
        # no auto-answer) — the silent failure the listener now warns about.
        dev = QHBoxLayout()
        dev.setSpacing(8)
        lbl = QLabel("Audio de la llamada")
        lbl.setObjectName("muted")
        self.cmb_speaker = QComboBox()
        self.cmb_speaker.setToolTip(
            "La salida de audio que escucho para oír a los demás.\n"
            "Debe ser la misma que usa Zoom / Meet / Teams como altavoz.")
        self._populate_speakers()
        self.cmb_speaker.currentIndexChanged.connect(self._on_speaker_changed)
        dev.addWidget(lbl)
        dev.addWidget(self.cmb_speaker, 1)
        root.addLayout(dev)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        # Answers: a scrolling feed of message cards
        ask = QWidget()
        av = QVBoxLayout(ask)
        av.setContentsMargins(0, 8, 0, 0)
        av.setSpacing(8)
        self.feed = QWidget()
        self.feed.setObjectName("feed")
        self.feed_lay = QVBoxLayout(self.feed)
        self.feed_lay.setContentsMargins(0, 0, 4, 0)
        self.feed_lay.setSpacing(8)
        self.empty = QLabel(EMPTY_FEED)
        self.empty.setObjectName("empty")
        self.empty.setTextFormat(Qt.MarkdownText)
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.feed_lay.addWidget(self.empty)
        self.feed_lay.addStretch(1)
        self.chat = QScrollArea()
        self.chat.setObjectName("chat")
        self.chat.setWidgetResizable(True)
        self.chat.setFrameShape(QFrame.NoFrame)
        self.chat.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)  # long code lines
        self.chat.setWidget(self.feed)
        self.chat.viewport().setAutoFillBackground(False)
        av.addWidget(self.chat, 1)
        inrow = QHBoxLayout()
        inrow.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Pregúntale a Claude…  (Enter, o pega una imagen)")
        self.input.returnPressed.connect(self.on_ask)
        self.btn_img = QPushButton("📎")
        self.btn_img.setObjectName("icon")
        self.btn_img.setFixedWidth(38)
        self.btn_img.setToolTip("Adjuntar imagen: usa la del portapapeles o elige un archivo")
        self.btn_img.clicked.connect(self.attach_image)
        inrow.addWidget(self.input, 1)
        inrow.addWidget(self.btn_img)
        av.addLayout(inrow)
        self.tabs.addTab(ask, "Respuestas")

        # Transcript
        tr = QWidget()
        tv = QVBoxLayout(tr)
        tv.setContentsMargins(0, 8, 0, 0)
        self.transcript = QTextBrowser()
        self.transcript.setPlaceholderText("Aquí aparece lo que se dice en la llamada.")
        tv.addWidget(self.transcript, 1)
        self.tabs.addTab(tr, "Transcripción")

        # Notes
        notes = QWidget()
        nv = QVBoxLayout(notes)
        nv.setContentsMargins(0, 8, 0, 0)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText(
            "Contexto para Claude: quién es el cliente, tu propuesta, precios, "
            "objeciones esperadas… (se guarda al cerrar)")
        nv.addWidget(self.notes)
        self.tabs.addTab(notes, "Notas")

        footer = QHBoxLayout()
        hint = QLabel(HOTKEY_HINT)
        hint.setObjectName("hint")
        footer.addWidget(hint)
        footer.addStretch()
        footer.addWidget(QSizeGrip(self), 0, Qt.AlignRight | Qt.AlignBottom)
        root.addLayout(footer)

    def _apply_style(self):
        self.setStyleSheet(
            """
            QWidget { color: #ECEEF2; font-family: 'Segoe UI'; font-size: 13px; }
            #title { font-size: 14px; font-weight: 600; }
            #status, #muted { color: #8B93A5; font-size: 11px; }
            #hint { color: #5C6476; font-size: 10px; }

            QTabWidget::pane { border: 0; }
            QTabBar::tab { background: transparent; color: #8B93A5; padding: 5px 12px;
                           margin-right: 4px; border-radius: 8px; font-weight: 600; }
            QTabBar::tab:hover { color: #ECEEF2; }
            QTabBar::tab:selected { background: rgba(255,255,255,0.08); color: #ECEEF2; }

            QScrollArea#chat, QWidget#feed { background: transparent; border: 0; }
            QLabel#empty { color: #8B93A5; font-size: 13px; padding: 36px 18px; }

            QFrame#cardAuto { background: rgba(240,138,93,0.11); border: 0;
                              border-left: 3px solid #F08A5D; border-radius: 10px; }
            QFrame#cardClaude { background: rgba(255,255,255,0.045); border-radius: 10px; }
            QFrame#cardYou { background: rgba(124,196,250,0.10); border-radius: 10px; }
            QLabel#cardHeader { font-size: 10px; font-weight: 700; letter-spacing: 1px;
                                color: #8B93A5; }
            QFrame#cardAuto QLabel#cardHeader { color: #F08A5D; }
            QFrame#cardYou QLabel#cardHeader { color: #7CC4FA; }
            QLabel#cardBody { font-size: 13px; color: #DDE1E8; }
            QFrame#cardAuto QLabel#cardBody { font-size: 15px; font-weight: 500;
                                              color: #FFFFFF; }

            QTextBrowser, QTextEdit, QLineEdit {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px; padding: 8px;
                selection-background-color: rgba(240,138,93,0.35); }
            QLineEdit:focus, QTextEdit:focus { border: 1px solid rgba(240,138,93,0.65); }

            QPushButton { background: rgba(255,255,255,0.07); border: 0; color: #ECEEF2;
                          border-radius: 9px; padding: 6px 12px; font-weight: 600; }
            QPushButton:hover { background: rgba(255,255,255,0.12); }
            QPushButton:pressed { background: rgba(255,255,255,0.16); }
            QPushButton#icon { padding: 6px 0; }
            QPushButton#chip { background: transparent; color: #8B93A5;
                               border: 1px solid rgba(255,255,255,0.10);
                               border-radius: 12px; padding: 4px 10px; font-size: 11px; }
            QPushButton#chip:checked { background: rgba(240,138,93,0.16); color: #FFC9AE;
                                       border: 1px solid rgba(240,138,93,0.45); }
            QPushButton#power { padding: 6px 14px; }
            QPushButton#power[on="true"]  { background: rgba(52,211,153,0.22); color: #A7F3D0; }
            QPushButton#power[on="true"]:hover  { background: rgba(52,211,153,0.34); }
            QPushButton#power[on="false"] { background: #F08A5D; color: #1A0F0A; }
            QPushButton#power[on="false"]:hover { background: #F49C75; }
            QPushButton#close { background: transparent; color: #8B93A5;
                                font-size: 16px; font-weight: 700; padding: 0; border-radius: 6px; }
            QPushButton#close:hover { background: rgba(239,68,68,0.40); color: #FFFFFF; }

            QComboBox { background: rgba(255,255,255,0.05);
                        border: 1px solid rgba(255,255,255,0.08);
                        border-radius: 8px; padding: 4px 10px; font-size: 12px; }
            QComboBox::drop-down { border: 0; width: 18px; }
            QComboBox QAbstractItemView { background: #151922; color: #ECEEF2;
                        border: 1px solid rgba(255,255,255,0.10);
                        selection-background-color: rgba(240,138,93,0.30); }

            QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.14);
                                          border-radius: 3px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
            QScrollBar::handle:horizontal { background: rgba(255,255,255,0.14);
                                            border-radius: 3px; min-width: 24px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
            QToolTip { background: #151922; color: #ECEEF2;
                       border: 1px solid rgba(255,255,255,0.12); padding: 4px 6px; }
            """
        )

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(22, 25, 34))
        grad.setColorAt(1.0, QColor(11, 13, 18))
        p.fillRect(self.rect(), grad)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), PANEL_RADIUS, PANEL_RADIUS)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), PANEL_RADIUS, PANEL_RADIUS)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _position_top_right(self):
        geo = self.screen().availableGeometry()
        self.move(geo.right() - self.width() - 24, geo.top() + 24)

    # ---------- capture exclusion ----------
    def showEvent(self, e):
        super().showEvent(e)
        self._set_capture_exclusion(True)

    def _set_capture_exclusion(self, on):
        try:
            hwnd = int(self.winId())
            ok = user32.SetWindowDisplayAffinity(
                hwnd, WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE
            )
            self._set_status("oculto al compartir" if ok else "¡VISIBLE! (falló)")
        except Exception:
            self._set_status("error de visibilidad")

    def _set_status(self, msg):
        """Show a status, elided to fit the header; the full text is the tooltip."""
        full = ("EN VIVO · " if self._live else "") + msg
        self.status.setToolTip(full)
        self.status.setText(self.status.fontMetrics().elidedText(
            full, Qt.ElideRight, self.status.maximumWidth()))

    # ---------- dragging ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 40:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, _):
        self._drag_pos = None

    # ---------- live mode ----------
    def toggle_live(self):
        if self._live:
            self._live = False
            self._retire_listener()
            self._set_power(False)
            self._set_status("detenido")
        else:
            self._live = True
            self._set_power(True)
            self._start_listener()

    def _set_power(self, on):
        """Reflect the live/idle state on the power switch: '● En vivo' (green) when
        listening, '● Escuchar' (coral call to action) when idle. Re-polish so the
        dynamic [on=...] stylesheet selector repaints."""
        self.btn_live.setText("●  En vivo" if on else "●  Escuchar")
        self.btn_live.setProperty("on", on)
        self.btn_live.style().unpolish(self.btn_live)
        self.btn_live.style().polish(self.btn_live)

    def _start_listener(self):
        """Spawn a Listener bound to the currently-selected call-audio device."""
        self._listener = Listener(speaker_device=self._selected_speaker())
        self._listener.line.connect(self._on_transcript)
        self._listener.status.connect(self._set_status)
        self._listener.finished.connect(self._reap_listener)
        self._listener.start()

    def _populate_speakers(self):
        """Fill the call-audio picker with output endpoints. The data role holds
        the device name ('' = system default) so the listener can pin it."""
        self.cmb_speaker.addItem("Automático (salida predeterminada)", "")
        for name, is_default in list_output_devices():
            self.cmb_speaker.addItem(f"{name}  ·  predeterminada" if is_default else name, name)

    def _selected_speaker(self):
        """The picked device name, or None for the system default."""
        return self.cmb_speaker.currentData() or None

    def _on_speaker_changed(self, _i):
        # Loopback endpoint is fixed at Listener spawn, so restart to apply it
        # live (mid-meeting device switch). No-op until the user goes live.
        if self._live:
            self._retire_listener()
            self._start_listener()

    def _retire_listener(self):
        """Tear down the active listener without blocking the UI.

        A running QThread must NOT be garbage-collected — Qt aborts the whole
        process with "QThread: Destroyed while thread is still running". So we
        (1) stop accepting its signals immediately, so no stale transcript line
        or ghost auto-answer bleeds in after the user toggled off; (2) hold a
        strong reference in self._retiring so the object cannot be collected
        while run() is still unwinding; and (3) let its own `finished` signal
        fire _reap_listener to drop that reference once the thread has actually
        terminated. We deliberately do not wait() here — that would freeze the
        UI for up to ~2.5s on every toggle.
        """
        lst = self._listener
        self._listener = None
        if not lst:
            return
        for sig, slot in ((lst.line, self._on_transcript),
                          (lst.status, self._set_status)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass  # already gone / never connected
        self._retiring.append(lst)
        lst.stop()

    def _reap_listener(self):
        """Drop our last reference to a listener once its thread has finished.

        Runs on the GUI thread (queued from the worker's `finished`), so mutating
        self._retiring needs no lock. Only now is destroying the QThread safe.
        """
        t = self.sender()
        if t in self._retiring:
            self._retiring.remove(t)

    def _on_transcript(self, text, source="Them"):
        now = time.monotonic()
        # Drop acoustic echo / mic-sidetone: a line that near-duplicates a recent
        # line from the OTHER source is the same utterance heard twice. The later
        # copy is the echo — discard it entirely (no log, no auto-trigger).
        if self._is_echo(text, source, now):
            return
        self._recent_lines.append((now, source, _norm_line(text)))
        self._recent_lines = [r for r in self._recent_lines if now - r[0] <= ECHO_WINDOW_S]

        # The buffer sent to Claude keeps the source keys; the panel shows Spanish labels.
        self._transcript_buf.append(f"{source}: {text}")
        color = THEM_COLOR if source == "Them" else YOU_COLOR
        self.transcript.append(
            f'<p style="margin:0 0 6px 0"><span style="color:{color}; font-weight:600">'
            f'{SOURCE_LABELS.get(source, source)}</span>&nbsp; {html.escape(text)}</p>')
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())
        # Only let the configured speaker(s) trigger an auto-answer — by default
        # the other side, so your own voice never makes Cluely answer itself.
        speaker_ok = AUTO_FROM == "both" or AUTO_FROM == source.lower()
        if self._live and self.chk_auto.isChecked() and speaker_ok:
            self._maybe_auto(text)

    def _is_echo(self, text, source, now):
        """True if `text` is your own mic ("You") picking up call audio that the
        loopback already captured as "Them" — acoustic echo from open speakers.
        We ONLY ever drop a "You" line: the call ("Them") source drives auto-answers
        and is ground truth for the other party, so it is never discarded even if it
        happens to arrive after a mic echo of the same words."""
        if not ECHO_GUARD or source != "You":
            return False
        norm = _norm_line(text)
        if len(norm) < 8:                       # too short to tell echo from a real backchannel
            return False
        for ts, src, prev in self._recent_lines:
            if src == source or now - ts > ECHO_WINDOW_S:
                continue
            if SequenceMatcher(None, norm, prev).ratio() >= ECHO_RATIO:
                return True
        return False

    def _looks_like_question(self, text):
        """Whether a transcript line is plausibly a question/prompt aimed at me.
        Lenient on purpose — a missed question is worse than an extra suggestion."""
        t = text.lower().strip()
        if any(h in t for h in QUESTION_HINTS):
            return True
        words = [w.strip(",.¿?¡!") for w in t.split()]
        while words and words[0] in LEADING_FILLERS:
            words.pop(0)
        return bool(words) and words[0] in QUESTION_WORDS

    def _maybe_auto(self, latest):
        # Must look like a question aimed at me before it's worth anything.
        if not self._looks_like_question(latest):
            return
        # Already answering: stash the newest follow-up and fire it on completion
        # (newest wins — by the time we're free, the latest question is the live one).
        if self._auto_inflight:
            self._pending_auto = latest
            return
        now = time.monotonic()
        total = sum(len(x) for x in self._transcript_buf)
        if now - self._last_auto < AUTO_THROTTLE_S:
            return
        if total - self._last_auto_len < AUTO_MIN_NEWCHARS:
            return
        self._fire_auto()

    def _fire_auto(self):
        """Start an auto-answer now. Records throttle/new-char baselines, marks the
        single-flight gate, and clears any queued follow-up it supersedes."""
        self._last_auto = time.monotonic()
        self._last_auto_len = sum(len(x) for x in self._transcript_buf)
        self._auto_inflight = True
        self._pending_auto = None
        self._start_worker(AUTO_PROMPT, self._recent_transcript(), "auto")

    def _drain_pending_auto(self):
        """Called when an auto-answer finishes. If a follow-up arrived mid-answer,
        fire it immediately — bypassing throttle/new-char gates, since it's an
        explicit queued question, not idle chatter."""
        if self._pending_auto is None:
            return
        if self._live and self.chk_auto.isChecked():
            self._fire_auto()            # clears _pending_auto
        else:
            self._pending_auto = None

    # ---------- Q&A ----------
    def on_ask(self):
        q = self.input.text().strip()
        if not q:
            return
        self.input.clear()
        self._convo.append(("you", q))
        self._render_chat()
        self._start_worker(q, self._recent_transcript(), "claude")

    def _recent_transcript(self):
        return "\n".join(self._transcript_buf)[-4000:]

    def ask_screen(self):
        """Capture the screen and ask Claude about it (on-demand vision)."""
        try:
            from vision import grab_screen_b64
            img = grab_screen_b64(VISION_MONITOR)
        except Exception as exc:
            self._convo.append(("you", "🖥️ captura de pantalla"))
            self._convo.append(("claude", f"_(no pude capturar la pantalla: {exc})_"))
            self._render_chat()
            return
        self._convo.append(("you", "🖥️ Te mandé mi pantalla: léela y dime qué responder"))
        self._render_chat()
        self._start_worker(VISION_PROMPT, self._recent_transcript(), "claude", image_b64=img)

    def attach_image(self):
        """Attach an image from the clipboard (preferred) or a file picker."""
        clip = QApplication.clipboard().image()
        if clip is not None and not clip.isNull():
            self._send_image(clip, "clipboard")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Elige una imagen", "",
            "Imágenes (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            self._set_status("no pude abrir esa imagen")
            return
        self._send_image(img, "file")

    def _send_image(self, img, source):
        b64 = _qimage_to_b64(img)
        q = self.input.text().strip()
        self.input.clear()
        tag = "imagen pegada" if source == "clipboard" else "imagen"
        self._convo.append(("you", f"🖼️ {tag}: {q or 'descríbela / resuélvela'}"))
        self._render_chat()
        # Empty box on an image = treat it as a full solve (same structured restate +
        # solution + read-aloud as the Solve-screen path).
        question = q or VISION_PROMPT
        self._start_worker(question, self._recent_transcript(), "claude", image_b64=b64)

    def eventFilter(self, obj, event):
        # Ctrl+V in the question box attaches a clipboard image instead of text.
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Paste):
                if not QApplication.clipboard().image().isNull():
                    self.attach_image()
                    return True
        return super().eventFilter(obj, event)

    def _start_worker(self, question, transcript, label, image_b64=""):
        idx = len(self._convo)
        self._convo.append((label, ""))
        self._render_chat()
        self.tabs.setCurrentIndex(0)
        self._set_status("leyendo pantalla…" if image_b64 else "pensando…")
        w = AskWorker(question, transcript, self.notes.toPlainText(), image_b64,
                      fast=(label == "auto"))
        w.chunk.connect(lambda s, i=idx: self._append_at(i, s))
        w.finished_text.connect(lambda _t, k=label: self._worker_done(k))
        w.failed.connect(lambda m, i=idx, k=label: self._fail_at(i, m, k))
        w.start()
        self._workers.append(w)
        self._workers = [x for x in self._workers if x.isRunning()] or self._workers

    def _append_at(self, i, s):
        role, text = self._convo[i]
        self._convo[i] = (role, text + s)
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _worker_done(self, kind="auto"):
        # Only the auto-answer worker owns the single-flight gate; a manual ask
        # finishing must not reopen it (and must not fire a queued follow-up).
        if kind == "auto":
            self._auto_inflight = False
            self._drain_pending_auto()
        self._set_status("oculto al compartir")

    def _fail_at(self, i, msg, kind="auto"):
        role, _ = self._convo[i]
        self._convo[i] = (role, f"_(error: {msg})_")
        self._render_chat()
        if kind == "auto":
            self._auto_inflight = False
            self._drain_pending_auto()
        self._set_status("error")

    def _render_chat(self):
        """Sync the card feed with self._convo: add cards for new entries and
        refresh the text of streaming ones. Roles never change once added."""
        bar = self.chat.verticalScrollBar()
        near_end = bar.value() >= bar.maximum() - 24
        added = len(self._convo) > len(self._cards)
        self.empty.setVisible(not self._convo)
        for i, (role, text) in enumerate(self._convo):
            if i == len(self._cards):
                card = MessageCard(role)
                self._cards.append(card)
                self.feed_lay.insertWidget(self.feed_lay.count() - 1, card)
            self._cards[i].set_text(text)
        # A new card always comes into view; a streaming one only keeps the view
        # pinned if you hadn't scrolled up to read something older. Scroll after
        # the layout has grown to fit the new text.
        if added or near_end:
            QTimer.singleShot(0, self._scroll_feed_to_end)

    def _scroll_feed_to_end(self):
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------- hotkey slots ----------
    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def focus_input(self):
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()
        self.tabs.setCurrentIndex(0)
        self.input.setFocus()

    def toggle_clickthrough(self):
        self._click_through = not self._click_through
        hwnd = int(self.winId())
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if self._click_through:
            ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            ex &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        self._set_status("el mouse atraviesa el panel" if self._click_through
                         else "oculto al compartir")

    def _quit(self):
        self.close()  # closeEvent does the listener teardown + notes save
        QApplication.quit()

    def _shutdown_listeners(self):
        """Stop and join the active and any retiring listeners. Blocking is fine
        here — the app is exiting — and a bounded wait() guarantees no QThread is
        destroyed while still running (which would abort the process)."""
        pending = list(self._retiring)
        if self._listener:
            pending.append(self._listener)
            self._listener = None
        for t in pending:
            t.stop()
            t.wait(5000)
        self._retiring.clear()

    def closeEvent(self, e):
        self._shutdown_listeners()
        try:
            (NOTES_DIR / "notes-latest.md").write_text(
                self.notes.toPlainText(), encoding="utf-8"
            )
        except Exception:
            pass
        super().closeEvent(e)

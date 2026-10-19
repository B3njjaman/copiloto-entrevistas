"""Live listening: captures the call (system audio via WASAPI loopback) AND your
own mic, transcribes both locally with faster-whisper, and emits labelled lines.

Loopback hears the *other* participants (whatever your speakers play); the mic
hears *you*. Each line is tagged "Them" or "You" so the rest of the app knows who
spoke — e.g. only the other side's questions should trigger an auto-answer. With
a calibrated voiceprint (voice.py), a mic phrase that isn't your voice is tagged
"Room": someone next to you in an in-person meeting, or your speakers' echo.

Capture runs as two producer threads (one per device) feeding a queue; a single
consumer runs whisper, so there is never a concurrent transcribe on one model.
"""
import os
import queue
import threading
import time

import numpy as np
from PySide6.QtCore import QThread, Signal

import voice
from config import (
    CAPTURE_MIC, CAPTURE_SYSTEM, MIC_DEVICE, SPEAKER_DEVICE, VOICE_ID, VOICE_THRESHOLD,
    WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_DIR, WHISPER_LANGUAGE, WHISPER_MODEL,
)

SAMPLE_RATE = 16000
# Speech is cut into segments at the speaker's pauses, so each transcript line is
# a whole phrase. Fixed 2s windows split questions in half ("¿Cuánto" | "tiempo
# les tomaría…"), which broke question detection and answered the wrong thing.
# A segment ends after PAUSE_SECONDS of quiet, or at CHUNK_SECONDS if the speaker
# never pauses. Lower PAUSE = faster pickup but more phrases cut mid-thought.
CHUNK_SECONDS = float(os.environ.get("CLUELY_CHUNK_SECONDS", "6.0"))
PAUSE_SECONDS = float(os.environ.get("CLUELY_PAUSE_SECONDS", "0.5"))
SUB_SECONDS = 0.25        # capture block: pause-detection granularity and stop latency
RMS_GATE = 0.006          # a block quieter than this counts as silence
_QUEUE_MAX = 8            # segments buffered before we drop the oldest to stay real-time
_LABEL = {"Them": "la llamada", "You": "tu micrófono"}  # status-line wording


def _add_cuda_dlls():
    """Put pip-installed CUDA libs on the DLL search path (GPU mode only)."""
    try:
        import importlib.util
        import pathlib
        spec = importlib.util.find_spec("nvidia")
        if not spec or not spec.submodule_search_locations:
            return
        base = pathlib.Path(spec.submodule_search_locations[0])
        for sub in ("cublas/bin", "cudnn/bin", "cuda_nvrtc/bin"):
            d = base / sub
            if d.is_dir():
                os.add_dll_directory(str(d))
    except Exception:
        pass


def list_output_devices():
    """Enumerate render endpoints for the UI 'Call audio' picker.

    Returns [(name, is_default), …]; an empty list if enumeration fails (the UI
    then just offers the auto/default option). WASAPI loopback is endpoint-
    specific, so the user must pick the SAME output device Zoom plays the call
    through — see _open_loopback / the README troubleshooting note.
    """
    try:
        import soundcard as sc
        default = ""
        try:
            default = sc.default_speaker().name
        except Exception:
            pass
        return [(spk.name, spk.name == default) for spk in sc.all_speakers()]
    except Exception:
        return []


class Listener(QThread):
    line = Signal(str, str)   # (text, source); source in {"Them", "You", "Room"}
    status = Signal(str)      # human-readable state

    def __init__(self, speaker_device=None, voiceprint=None):
        super().__init__()
        # Your calibrated voiceprint (voice.load_profile()), or None to tag every
        # mic phrase "You" as before.
        self._voiceprint = voiceprint if VOICE_ID else None
        self._room_heard = False
        self._stop = threading.Event()
        self._model = None
        self._q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._cap_threads: list[threading.Thread] = []
        # Explicit loopback endpoint chosen in the UI ("" / None = system default).
        self._speaker_device = speaker_device
        self._loopback_name = ""
        # Per-source count of chunks that passed the RMS gate. Used to detect the
        # silent-loopback (wrong-endpoint) failure: mic active but call dead.
        self._passed = {"Them": 0, "You": 0}
        self._warned_silent = False
        self._warned_decode = False

    def stop(self):
        self._stop.set()

    def _load_model(self):
        from faster_whisper import WhisperModel
        if WHISPER_DEVICE == "cuda":
            _add_cuda_dlls()
            try:
                self.status.emit(f"cargando whisper ({WHISPER_MODEL}, GPU)…")
                model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type=WHISPER_COMPUTE,
                                     download_root=WHISPER_DIR)
                # A missing cuBLAS/cuDNN DLL often fails only on the first decode,
                # so run one now (segments are lazy: list() forces it) while the
                # CPU fallback below can still catch it.
                segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                                               language=WHISPER_LANGUAGE, beam_size=1)
                list(segments)
                return model
            except Exception as exc:
                self.status.emit(f"GPU no disponible ({type(exc).__name__}); uso CPU")
        self.status.emit(f"cargando whisper ({WHISPER_MODEL}, CPU)…")
        return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                            download_root=WHISPER_DIR)

    def _open_loopback(self, sc):
        name = self._loopback_name or self._speaker_device or SPEAKER_DEVICE \
            or sc.default_speaker().name
        return sc.get_microphone(str(name), include_loopback=True)

    def _open_mic(self, sc):
        if MIC_DEVICE:
            return sc.get_microphone(str(MIC_DEVICE), include_loopback=False)
        return sc.default_microphone()

    def _capture(self, source, opener):
        """Producer: record one device in small blocks and cut the stream into
        phrases at pauses (see PAUSE_SECONDS), pushing (source, audio) onto the
        queue. Silence between phrases is never sent to whisper, which
        hallucinates on it. Device errors are per-source, so one dead device
        never silences the other."""
        import soundcard as sc
        try:
            dev = opener(sc)
        except Exception as exc:
            self.status.emit(f"sin audio de {_LABEL[source]} ({type(exc).__name__})")
            return
        sub = int(SAMPLE_RATE * SUB_SECONDS)
        max_blocks = max(1, round(CHUNK_SECONDS / SUB_SECONDS))
        pause_blocks = max(1, round(PAUSE_SECONDS / SUB_SECONDS))
        try:
            # blocksize sets the WASAPI buffer. soundcard's default is one device
            # period (~10 ms), so any stall of this thread while whisper decodes
            # dropped audio ("data discontinuity") and lost words. 1 s of buffer
            # adds no latency: blocks are still read as soon as they arrive.
            with dev.recorder(samplerate=SAMPLE_RATE, channels=1,
                              blocksize=SAMPLE_RATE) as rec:
                seg: list = []   # blocks of the phrase in progress
                quiet = 0        # consecutive quiet blocks at its end
                pre = None       # last quiet block, kept so a soft onset isn't clipped
                while not self._stop.is_set():
                    block = np.asarray(rec.record(numframes=sub), dtype=np.float32).reshape(-1)
                    if not block.size:
                        continue
                    loud = float(np.sqrt(np.mean(block * block))) >= RMS_GATE
                    if not seg:
                        if loud:
                            seg = [block] if pre is None else [pre, block]
                            quiet = 0
                        else:
                            pre = block
                        continue
                    seg.append(block)
                    quiet = 0 if loud else quiet + 1
                    if quiet >= pause_blocks or len(seg) >= max_blocks:
                        self._push(source, np.concatenate(seg))
                        # Reuse the trailing quiet block as the next phrase's
                        # lead-in: whisper drops the first word ("¿Cuánto…")
                        # when audio starts abruptly on it.
                        pre = None if loud else block
                        seg, quiet = [], 0
        except Exception as exc:
            self.status.emit(f"error escuchando a {_LABEL[source]}: {type(exc).__name__}")

    def _label(self, source, audio):
        """Who said a phrase: a mic ("You") phrase that doesn't match your
        voiceprint becomes "Room". Too-short phrases keep the mic's "You", since
        a voiceprint of under a second isn't reliable."""
        if source != "You" or self._voiceprint is None:
            return source
        try:
            score = voice.similarity(audio, self._voiceprint)
        except Exception:
            return source
        if score is not None and score < VOICE_THRESHOLD:
            self._room_heard = True
            return "Room"
        return source

    def _push(self, source, audio):
        """Queue one phrase for the transcriber, dropping the oldest when full."""
        self._passed[source] += 1
        try:
            self._q.put_nowait((source, audio))
        except queue.Full:
            try:
                self._q.get_nowait()              # drop oldest, keep recent
                self._q.put_nowait((source, audio))
            except (queue.Empty, queue.Full):
                pass  # raced with the other producer; just drop this phrase

    def run(self):
        # Resolve the loopback endpoint name ONCE so the producer opens a fixed
        # device and the status line can show the user exactly what's captured.
        if CAPTURE_SYSTEM:
            try:
                import soundcard as sc
                self._loopback_name = (
                    self._speaker_device or SPEAKER_DEVICE or sc.default_speaker().name
                )
            except Exception:
                self._loopback_name = ""

        sources = []
        if CAPTURE_SYSTEM:
            sources.append(("Them", self._open_loopback))
        if CAPTURE_MIC:
            sources.append(("You", self._open_mic))
        if not sources:
            self.status.emit("no hay fuentes de audio activas")
            return

        # Capture starts before the model loads (that can take ~15s from a slow
        # disk): chunks wait in the queue (up to _QUEUE_MAX) and are transcribed
        # once it's ready, so the first question of the call isn't lost.
        for src, opener in sources:
            t = threading.Thread(target=self._capture, args=(src, opener), daemon=True)
            t.start()
            self._cap_threads.append(t)

        try:
            self._model = self._load_model()
        except Exception as exc:
            self.status.emit(f"error al cargar whisper: {exc}")
            self._stop.set()
            for t in self._cap_threads:
                t.join(timeout=2.0)
            return

        if self._voiceprint is not None:
            try:
                voice.warm()   # load (first time: download) the model before phrases arrive
            except Exception as exc:
                self._voiceprint = None
                self.status.emit(f"sin separación de voz ({type(exc).__name__})")

        label = "escuchando (tú + llamada)" if len(sources) == 2 else "escuchando"
        if self._voiceprint is not None:
            label += " · voz ✓"
        if CAPTURE_SYSTEM and self._loopback_name:
            label += f" · llamada: {self._loopback_name}"
        self.status.emit(label)

        # Consumer: serialize whisper across both sources (one model, no races).
        listen_start = time.monotonic()
        while not self._stop.is_set():
            # Wrong-endpoint detector: if the mic is clearly active (a real
            # conversation) but the loopback has produced ZERO audio for a while,
            # Cluely is almost certainly listening to the wrong speaker — the
            # exact silent failure where Zoom's output device differs from this
            # one. Keying off "mic alive, call dead" avoids the false positive a
            # naive startup RMS probe would hit during legitimate silence.
            # Other voices on the mic mean an in-person meeting, where a silent
            # call is expected, so the warning is skipped then.
            if (not self._warned_silent and CAPTURE_SYSTEM and CAPTURE_MIC
                    and not self._room_heard
                    and self._passed["You"] >= 2 and self._passed["Them"] == 0
                    and time.monotonic() - listen_start > 25):
                self._warned_silent = True
                self.status.emit("⚠ no oigo la llamada: elige la salida que usa Zoom/Meet")
            try:
                source, audio = self._q.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                segments, _ = self._model.transcribe(
                    audio, language=WHISPER_LANGUAGE, beam_size=1, vad_filter=True
                )
                text = " ".join(s.text.strip() for s in segments).strip()
            except Exception as exc:
                if not self._warned_decode:     # say it once instead of failing silently
                    self._warned_decode = True
                    self.status.emit(f"error al transcribir: {type(exc).__name__}")
                continue
            if text:
                self.line.emit(text, self._label(source, audio))

        for t in self._cap_threads:
            t.join(timeout=2.0)
        self.status.emit("detenido")

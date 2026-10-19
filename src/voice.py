"""Voiceprint: tells your voice apart from everyone else's on the microphone.

Calibration records you reading a short text, turns it into a speaker embedding
(a "voiceprint") with a small ONNX model run by sherpa-onnx, and saves it. Live,
each microphone phrase gets its own embedding and counts as yours when its
cosine similarity to the voiceprint reaches VOICE_THRESHOLD.

The voiceprint is biometric data: it is stored only in profile/ (gitignored)
and never sent anywhere. Only the model file is downloaded, once.
"""
import json
import threading
import time
import urllib.request

import numpy as np

from config import MODELS_DIR, PROFILE_DIR

# WeSpeaker ResNet34 trained on VoxCeleb (speakers of many languages), 26 MB.
MODEL_NAME = "wespeaker_en_voxceleb_resnet34_LM.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/" + MODEL_NAME)
SAMPLE_RATE = 16000
MIN_PHRASE_SECONDS = 1.0     # shorter phrases give unreliable voiceprints
ENROLL_MIN_SECONDS = 6.0     # speech needed (silence removed) to calibrate
ENROLL_WINDOW_SECONDS = 3.0  # calibration speech is embedded in windows this long
MIN_CONSISTENCY = 0.6        # every window must look like the same voice
_SILENCE_RMS = 0.006         # same gate as the listener: quieter blocks are silence
_BLOCK = SAMPLE_RATE // 4

PROFILE_FILE = PROFILE_DIR / "voice.npy"
META_FILE = PROFILE_DIR / "voice.json"

CALIBRATION_TEXT = (
    "Hola, estoy calibrando mi voz para que el copiloto sepa cuándo hablo yo. "
    "Hoy vamos a revisar la propuesta, los plazos y el presupuesto. Si tienen "
    "preguntas sobre la implementación, los datos o los precios, con gusto las "
    "respondo."
)

_extractor = None
_extractor_lock = threading.Lock()   # used from the listener and calibration threads


class CalibrationError(Exception):
    """Calibration audio can't make a reliable voiceprint; the message says why
    in words the user can act on."""


def model_path():
    """The voiceprint model, downloaded on first use."""
    path = MODELS_DIR / MODEL_NAME
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        urllib.request.urlretrieve(MODEL_URL, partial)
        partial.replace(path)
    return path


def _get_extractor():
    global _extractor
    with _extractor_lock:
        if _extractor is None:
            import sherpa_onnx
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(model_path()), num_threads=1, provider="cpu")
            if not config.validate():
                raise RuntimeError(f"invalid voiceprint model: {model_path()}")
            _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        return _extractor


def warm():
    """Load the model now (downloading it the first time) so the first live
    phrase isn't slowed down by it."""
    _get_extractor()


def embed(audio):
    """L2-normalised voiceprint of one stretch of 16 kHz mono audio."""
    extractor = _get_extractor()
    with _extractor_lock:
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE,
                               waveform=np.asarray(audio, dtype=np.float32))
        stream.input_finished()
        vector = np.asarray(extractor.compute(stream), dtype=np.float32)
    return vector / (np.linalg.norm(vector) + 1e-9)


def speech_only(audio):
    """Drop the quiet quarter-second blocks so pauses don't dilute the voiceprint."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    blocks = [audio[i:i + _BLOCK] for i in range(0, len(audio), _BLOCK)]
    loud = [b for b in blocks if b.size and np.sqrt(np.mean(b * b)) >= _SILENCE_RMS]
    return np.concatenate(loud) if loud else np.zeros(0, dtype=np.float32)


def enroll(audio):
    """Build a voiceprint from calibration audio.

    The speech is cut into ENROLL_WINDOW_SECONDS windows, each is embedded, and
    the voiceprint is their normalised mean. `consistency` is the lowest window
    similarity to that mean: a low value means another voice or a lot of noise
    got into the recording. Raises CalibrationError when the result can't be
    trusted. Returns {"embedding", "consistency", "seconds"}.
    """
    speech = speech_only(audio)
    seconds = len(speech) / SAMPLE_RATE
    if seconds < ENROLL_MIN_SECONDS:
        raise CalibrationError(
            f"Te escuché solo {seconds:.0f} s. Lee el texto completo, un poco más "
            "fuerte o más cerca del micrófono.")
    window = int(ENROLL_WINDOW_SECONDS * SAMPLE_RATE)
    parts = [speech[i:i + window] for i in range(0, len(speech) - window + 1, window)]
    vectors = np.stack([embed(p) for p in parts])
    mean = vectors.mean(axis=0)
    mean /= np.linalg.norm(mean) + 1e-9
    consistency = float((vectors @ mean).min())
    if consistency < MIN_CONSISTENCY:
        raise CalibrationError(
            "La grabación no salió pareja (¿habló alguien más o hay mucho ruido?). "
            "Inténtalo de nuevo en silencio.")
    return {"embedding": mean, "consistency": consistency, "seconds": seconds}


def save_profile(result):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(PROFILE_FILE, result["embedding"])
    META_FILE.write_text(json.dumps({
        "model": MODEL_NAME,
        "consistency": round(result["consistency"], 3),
        "seconds": round(result["seconds"], 1),
        "created": time.strftime("%Y-%m-%d %H:%M"),
    }, indent=2), encoding="utf-8")


def load_profile():
    """Your saved voiceprint, or None if there is none (or it came from a
    different model, whose embeddings wouldn't be comparable)."""
    try:
        meta = json.loads(META_FILE.read_text(encoding="utf-8"))
        if meta.get("model") != MODEL_NAME:
            return None
        return np.load(PROFILE_FILE).astype(np.float32)
    except (OSError, ValueError):
        return None


def similarity(audio, profile):
    """Cosine similarity of a phrase to the voiceprint, or None when the phrase
    is too short to judge."""
    speech = speech_only(audio)
    if len(speech) < MIN_PHRASE_SECONDS * SAMPLE_RATE:
        return None
    return float(embed(speech) @ profile)

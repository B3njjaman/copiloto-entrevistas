"""Voiceprint check with Windows text-to-speech voices as stand-in speakers.
Run with the venv python:
    .venv\\Scripts\\python.exe tests\\test_voice.py
Calibrates on one installed voice, then checks that its phrases score as "You"
and other voices' phrases as "Them" at VOICE_THRESHOLD. Prints the scores.
Downloads the voiceprint model on first run. Exits 0 + OK, or prints FAIL."""
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import voice  # noqa: E402
from config import VOICE_THRESHOLD  # noqa: E402

PHRASES = [
    "¿Cuánto tiempo les tomaría implementar el programa en nuestras tiendas?",
    "Nosotros ya trabajamos con otro proveedor, pero no estamos conformes.",
    "¿Y qué pasa con los datos de nuestros clientes, de quién son?",
]

PS_LIST = ("Add-Type -AssemblyName System.Speech; "
           "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() "
           "| ForEach-Object { $_.VoiceInfo.Name }")
PS_SPEAK = ("Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.SelectVoice($env:VOICE); "
            "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
            "[System.Speech.AudioFormat.AudioChannel]::Mono); "
            "$s.SetOutputToWaveFile($env:OUT, $f); $s.Speak($env:TEXT); $s.Dispose()")


def powershell(script, **env):
    return subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          env=dict(os.environ, **env), capture_output=True,
                          text=True, check=True).stdout


def synthesize(voice_name, text, path):
    powershell(PS_SPEAK, VOICE=voice_name, TEXT=text, OUT=path)
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0 * 0.4   # roughly a mic's level


def main():
    voices = [v.strip() for v in powershell(PS_LIST).splitlines() if v.strip()]
    if len(voices) < 2:
        print("SKIP: needs at least two Windows TTS voices")
        return
    me, others = voices[-1], voices[:-1]
    tmp = tempfile.mkdtemp()
    calibration = synthesize(me, voice.CALIBRATION_TEXT, os.path.join(tmp, "cal.wav"))
    result = voice.enroll(calibration)
    print(f"calibrated on {me}: consistency={result['consistency']:.2f}, "
          f"{result['seconds']:.1f}s of speech, threshold={VOICE_THRESHOLD}")

    failures = []
    for name in [me] + others:
        for i, text in enumerate(PHRASES):
            audio = synthesize(name, text, os.path.join(tmp, f"{i}.wav"))
            score = voice.similarity(audio, result["embedding"])
            expect_me = name == me
            ok = score is not None and (score >= VOICE_THRESHOLD) == expect_me
            print(f"  {'You ' if expect_me else 'Them'} {name:28s} {score:.2f} "
                  f"{'ok' if ok else 'WRONG'}")
            if not ok:
                failures.append((name, text, score))

    # The listener tags mic phrases by voice; call audio is never relabelled.
    from PySide6.QtCore import QCoreApplication
    from ears import Listener
    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    listener = Listener(voiceprint=result["embedding"])
    mine = synthesize(me, PHRASES[0], os.path.join(tmp, "mine.wav"))
    theirs = synthesize(others[0], PHRASES[0], os.path.join(tmp, "theirs.wav"))
    for source, audio, expected in (("You", mine, "You"), ("You", theirs, "Room"),
                                    ("Them", mine, "Them")):
        got = listener._label(source, audio)
        if got != expected:
            failures.append(("label", source, expected, got))
    print(f"  listener labels: mine->{listener._label('You', mine)}, "
          f"theirs->{listener._label('You', theirs)}")

    # Silence or a too-short phrase must not be judged at all.
    assert voice.similarity(np.zeros(8000, dtype=np.float32), result["embedding"]) is None
    try:
        voice.enroll(np.zeros(16000 * 3, dtype=np.float32))
        failures.append(("enroll", "silence accepted", None))
    except voice.CalibrationError:
        pass

    if failures:
        print(f"FAIL: {failures}")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()

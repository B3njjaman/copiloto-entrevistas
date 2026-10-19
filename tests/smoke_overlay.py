"""Headless smoke test for the on-screen controls. Run with the venv python:
    .venv\\Scripts\\python.exe tests\\smoke_overlay.py
Exits 0 + prints OK on success; prints FAIL and exits 1 otherwise.
Uses Qt's offscreen platform so it needs no display. Does NOT call toggle_live(True)
(that would start the real audio listener) - it exercises _set_power directly."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication


def main():
    app = QApplication.instance() or QApplication([])
    from overlay import Overlay
    ov = Overlay()

    # close button exists and is labelled
    assert hasattr(ov, "btn_close") and ov.btn_close is not None, "btn_close missing"
    assert ov.btn_close.text() == "×", "close button label"

    # power switch: object name + idle defaults
    assert ov.btn_live.objectName() == "power", "power objectName"
    assert ov._live is False, "should start idle"
    assert ov.btn_live.text() == "●  Escuchar", f"idle label was {ov.btn_live.text()!r}"
    assert ov.btn_live.property("on") is False, "idle property"

    # _set_power flips label + property (pure UI, no listener)
    ov._set_power(True)
    assert ov.btn_live.text() == "●  En vivo", "live label"
    assert ov.btn_live.property("on") is True, "live property"
    ov._set_power(False)
    assert ov.btn_live.text() == "●  Escuchar", "back to idle label"
    assert ov.btn_live.property("on") is False, "back to idle property"

    # Someone else on the mic ("Room") shows and reaches Claude as the other side
    ov._on_transcript("Hola, ¿cómo están todos hoy en la oficina?", "Room")
    assert ov._transcript_buf[-1].startswith("Them: "), ov._transcript_buf[-1]
    assert "Ellos" in ov.transcript.toPlainText(), "Room should display as Ellos"
    # ...and a mic echo of call audio is dropped whether it is tagged You or Room
    n = len(ov._transcript_buf)
    ov._on_transcript("Los datos de sus clientes son de ustedes", "Them")
    ov._on_transcript("Los datos de sus clientes son de ustedes", "Room")
    ov._on_transcript("los datos de sus clientes son de ustedes.", "You")
    assert len(ov._transcript_buf) == n + 1, ov._transcript_buf[n:]

    # Voice calibration. Nothing here records or touches the real profile/.
    import numpy as np
    import voice
    voice.save_profile = lambda result: None
    went_live = []
    ov._go_live = lambda: went_live.append(True)   # instead of the real listener
    ov._voiceprint, ov._voice_skipped = None, False
    ov._update_voice_chip()
    assert ov.btn_voice.text() == "Calibrar voz", ov.btn_voice.text()
    # The first Escuchar without a voiceprint opens the card instead of listening
    ov.toggle_live()
    assert not ov.calib.isHidden() and ov.tabs.isHidden(), "card should replace the tabs"
    assert not went_live and ov._listener is None, "must not listen before calibrating"
    ov.toggle_live()                                # ignored while the card is open
    assert not went_live
    # A failed recording offers a retry
    ov._on_calibration_failed("Te escuché solo 2 s.")
    assert ov.calib_start.text() == "Reintentar" and ov.calib_start.isEnabled()
    # A good one updates the chip, then the card closes and listening starts
    ov._on_calibrated({"embedding": np.ones(256, dtype=np.float32),
                       "consistency": 0.9, "seconds": 12.0})
    assert ov.btn_voice.text() == "Mi voz ✓" and "90%" in ov.calib_msg.text()
    ov._finish_calibration()
    assert ov.calib.isHidden() and not ov.tabs.isHidden(), "tabs should come back"
    assert went_live == [True], "should go live after calibrating"
    # Omitir listens without voice ID and doesn't ask again this session
    went_live.clear()
    ov._voiceprint = None
    ov._show_calibration(then_listen=True)
    assert ov.calib_skip.text() == "Omitir"
    ov._skip_calibration()
    assert ov._voice_skipped and went_live == [True] and ov.calib.isHidden()
    went_live.clear()
    ov.toggle_live()
    assert went_live == [True] and ov.calib.isHidden(), "skipped: listen straight away"

    # The mascot (only with a personal skin in skin/) follows the app's state
    if ov.mascot is not None:
        ov._live = True                  # as _go_live would, without a listener
        ov._set_power(True)
        assert ov.mascot.state == "listening", ov.mascot.state
        ov._mascot("answering", "¡Listo!")
        assert ov.mascot.state == "answering"
        ov._mascot_rest()
        assert ov.mascot.state == "listening", "should settle back to listening"
        ov._live = False
        ov._set_power(False)
        assert ov.mascot.state == "idle"
    print("mascot:", "on" if ov.mascot is not None else "off (no skin/)")

    print("OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FAIL: {e}"); sys.exit(1)
    except Exception as e:
        print(f"FAIL (error): {type(e).__name__}: {e}"); sys.exit(1)

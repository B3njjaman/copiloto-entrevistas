# Changelog

All notable changes are recorded here. Format based on Keep a Changelog.
Versioning is informal (personal tool). Versions up to 0.3.0 are the upstream
project, ron2k1/Ronils-Cluely-OPENSOURCE.

## [Unreleased]

## [1.0.0] - 2026-10-19
Fork renamed to **Copiloto**, adapted for Spanish-speaking meetings.
### Changed
- Lean Claude session: the warm `claude -p` process now starts with no tools, no
  MCP servers, no user plugins/hooks/skills, no CLAUDE.md, a short custom system
  prompt and no session persistence. Context per turn drops from ~2.8k to ~1k
  tokens, nothing said on a call can make Claude run a command or read a file,
  and meeting text is not written to `~/.claude`. It still uses the normal
  `claude` login, so it runs on your subscription.
- `claude.exe` is launched directly; `cmd /c` is only used for an npm `.cmd` shim.
- Transcription defaults to Spanish with the multilingual `small` model on the
  GPU (falls back to CPU). New `CLUELY_WHISPER_LANGUAGE` (`es`, `en`, …, `auto`).
- Question detection understands Spanish (`¿`, qué, cómo, cuánto, cuéntame…) as
  well as English; answers come back in the language of the meeting.
- Redesigned overlay: Spanish UI, answers as cards (suggestions highlighted in
  coral and larger so they can be read at a glance), colored speaker labels in the
  transcript, segmented tabs, coral call-to-action power button, hotkey footer.
- Audio is cut into phrases at the speaker's pauses (`CLUELY_PAUSE_SECONDS`,
  default 0.5s; `CLUELY_CHUNK_SECONDS` is now the longest phrase, default 6s)
  instead of fixed 2s windows that split questions in half. Capture starts
  before the whisper model finishes loading, so the start of a call isn't lost.
- Live answers are only the words to say, without "they're asking…" lead-ins.
### Fixed
- **The panel was visible in screen shares on Windows 10.** `SetWindowDisplayAffinity`
  fails with error 8 on `WA_TranslucentBackground` (per-pixel alpha) windows, so
  the exclusion never applied and the status read "VISIBLE (failed)". The panel is
  now an opaque window with a rounded window mask plus global opacity, and the
  exclusion holds (also with click-through and opacity changes).
- Words were lost at phrase starts ("¿Cuánto…" became "Tiempo…"): soundcard's
  default WASAPI buffer is one device period (~10 ms), so any stall of the capture
  thread while whisper decoded dropped audio. The recorder now uses a 1 s buffer.
- The power glyph `⏻` rendered as a box in Segoe UI; it is now `●`.
- A GPU that loads but fails on the first decode (missing cuBLAS/cuDNN) now falls
  back to CPU at load time, and decode errors show in the status instead of the
  transcript silently staying empty.
- Links in answers only open `http(s)` URLs; other schemes are ignored.
### Added
- `copiloto.env` file for settings (the desktop shortcut can't carry env vars),
  with `copiloto.env.example`.
- `CLUELY_WHISPER_DIR` to download whisper models to another folder or drive.
- `install_shortcut.ps1 -Venv <path>` for a virtualenv outside the repo.
- `scripts/screenshots.py` renders the README screenshots with demo data.

## [0.3.0] - 2026-06-20
### Changed
- Public release. The repository is now public.
- Notes are local-only. `notes/` is gitignored and never committed, reversing the
  0.2.0 decision to version it. Meeting transcripts hold other participants' words
  and must not leave your machine.
### Added
- `LICENSE` (Apache-2.0).
- `CLUELY_MY_NAME` so auto-answer can tell a question is aimed at you by name.
  No personal name is hardcoded in the source anymore.
- README setup section: Windows requirements, venv, `pip install`, and the
  `claude` CLI login, plus a data-flow diagram.
### Fixed
- The `claude` CLI is resolved from PATH first, so Cluely works whether the CLI
  came from the native installer, winget, or npm. `CLUELY_CLAUDE_CMD` still
  overrides.
- `scripts/install_shortcut.ps1` derives its base path at runtime instead of a
  hardcoded user path, so the launcher works from any clone location.
### Removed
- Internal design and planning docs are no longer tracked in the repo.

## [0.2.0] - 2026-06-20
### Added
- On-screen power toggle (⏻ OFF/LIVE) and a visible close (×) button on the overlay.
- Console-less Desktop + Start Menu launcher shortcut with a generated app icon.
- This repository, with dated history and this changelog.
### Notes
- Notes versioning was enabled here. It was reversed in 0.3.0: notes are
  local-only and never committed.

## [0.1.0] - 2026-06-19
### Added
- Initial Cluely: capture-invisible PySide6 overlay (SetWindowDisplayAffinity /
  WDA_EXCLUDEFROMCAPTURE), warm headless `claude` backend (no API key), live WASAPI
  loopback + mic transcription via faster-whisper, auto-answer of questions aimed at
  you, on-demand screen vision + image attach, notes tab, and global hotkeys.

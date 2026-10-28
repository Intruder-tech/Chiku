# Changelog

All notable changes to this project are listed here. Versions follow [SemVer](https://semver.org).

## [0.5.1] - 2026-10-28

### Fixed
- **Installed app could stop responding to voice after first-run setup.** On PCs without the
  NVIDIA speech pack, the first CUDA attempt failed (as expected) and fell back to the CPU, but
  the reload after onboarding tried CUDA again. A second CUDA initialisation in the same process
  hangs inside CTranslate2, so voice turns never completed. Chiku now checks that the NVIDIA
  DLLs load *before* touching CUDA, never retries CUDA after a failure, and serialises model
  loading. The CPU fallback also starts faster (about 1 s instead of 7–8 s).
- `chiku-cli.exe` crashed when printing non-ASCII text (for example "→") because the packaged
  console used the ANSI code page; output is now UTF-8.

## [0.5.0] - 2026-10-28 (first public release candidate)

### Added
- **Branding.** The official Chiku mascot is used throughout: app and taskbar icon, a header
  avatar, an animated mascot driven by real listening and speaking levels, a welcome screen,
  the onboarding wizard, and the installer artwork. All brand assets come from one module,
  `chiku.branding`.
- **First-run setup.** A wizard detects the CPU, GPU (any vendor) and RAM, then recommends
  models that fit the machine. Downloads happen only after you confirm. On NVIDIA PCs it offers
  an optional speech pack that is checksum-verified against PyPI.
- **Windows installer** (Inno Setup):
  - installs for the current user (no admin rights needed)
  - Start menu shortcut, optional desktop shortcut
  - clean upgrades that keep user data
  - uninstall asks whether to keep your personal data
- **Performance tooling.** `python -m chiku --benchmark` measures startup time, speech
  recognition latency and word error rate, voice synthesis speed, LLM first token, full voice
  turns, cancellation, and CPU/GPU/RAM use. Results go to a JSON and Markdown report.
- `--version-info` checks that every native component loads; used as the packaged-app smoke
  test.
- Open-source packaging:
  - GPL-3.0-or-later licence, plus a separate mascot licence
  - third-party notices generated from the real dependency tree
  - contributing guide
  - CI and release workflows

### Changed
- The current time moved from the system prompt into the newest user message. The system
  prompt and tool definitions now stay cached in Ollama, so the first token after the clock
  changes dropped from 2.07 s to 0.16 s (measured A/B on an RTX 5050).
- Voice, speech recognition and the LLM now load in parallel at startup.
- The first spoken chunk is released sooner (28 characters instead of 40).

### Earlier phases (0.1–0.4, unreleased)
- Phase 1–2: live voice loop (Silero VAD, Whisper, Ollama, Kokoro), barge-in, streaming
  speech, verified app launching.
- Phase 3: web research with sources (Brave), sandboxed file tools, window and media control.
- Phase 4: user-controlled memory, OpenAI-compatible and cloud providers (opt-in), model
  manager, tool switches, activity history.

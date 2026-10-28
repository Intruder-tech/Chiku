# Changelog

All notable changes to this project are listed here. Versions follow [SemVer](https://semver.org).

## [0.5.5] - 2026-10-29

### Added

- **Send messages on Discord and WhatsApp Web.** "Text Manas on Discord saying I'll be
  late" opens the chat, types the message and asks you to confirm before sending. Works in
  English and Hindi requests; no model call is needed for the common phrasings.
- **App control.** New tools read the buttons, tabs and fields of an open app, press one by
  name, type text and press key combos, through Windows UI Automation and the keyboard. Each
  action that changes something asks first. Terminals, PowerShell and admin tools are refused.
- Tools can now ask for a confirmation in the middle of a run; the answer is in the audit log.

### Security

- Before typing anything the messaging tool checks which control has the keyboard focus, and
  checks again before pressing Enter. If a game, pop-up or other field has the focus, it stops
  without typing. (An early test build typed a chat name while a Discord Activity held the
  focus; that build was never released.)

### Dependencies

- comtypes 1.4.17 (MIT), for Windows UI Automation.

## [0.5.4] - 2026-10-28

### Changed

- New look, in the style of desktop chat apps:
  - **Chat:** a new chat greets you by name in the middle of the window, with the message
    box and a few suggestions underneath. Once you send something, the greeting is gone for
    that chat, the conversation fills the window and the message box moves to the bottom.
    The mascot picture is gone.
  - **Messages:** Chiku's replies are plain text beside her avatar, in a centred reading
    column; yours are bubbles on the right.
  - **Sidebar:** New chat, Search (Ctrl+K), pages with icons, Recents, and you, with a
    light/dark switch.
  - **Status:** a slim strip above the chat shows Chiku's live state and voice level. The
    side panel is now off until you ask for it (the button in that strip, or Ctrl+.).
  - **Settings:** sections listed on the left, each with a title, description and card;
    on/off switches instead of tick boxes; Save changes / Cancel in a footer. A new
    **Appearance** section has a System / Light / Dark switch that applies at once, the
    minimised style, and "keep running when closed".
  - **Pages:** Profile, Apps, Memory and Activity share the same page headers, cards and
    tidy tables; Memory and Activity lost their dialog-style Close buttons.
  - Icons come from Windows' own icon font; the colours are warmer neutrals with Chiku's
    yellow as the accent.
- Closing the window now keeps Chiku running in the tray (`ui.close_to_tray`, on by
  default), so the wake word and the Discord status stay on. Quit from the tray menu.
- Discord presence shows the repository on its own line ("Free & open source ·
  github.com/Intruder-tech/Chiku") with a single **Install Chiku from GitHub** button.

### Added

- Project website in `website/`, published with GitHub Pages
  (`.github/workflows/pages.yml`).
- `scripts/build_assets.py` also makes 1024×1024 Discord art assets
  (`assets/branding/discord/`). Discord needs at least 512×512; the original mascot file is
  500×500.

## [0.5.3] - 2026-10-28

### Added

- Multilingual conversation. With the multilingual Whisper model (Settings → Languages, one
  ~484 MB download), Chiku detects the language of each utterance and replies in it.
  - English and Hindi are first-class. When Whisper labels Hindi speech as Urdu (common), it
    is transcribed as Hindi, and unsure detections fall back to the languages you speak.
  - Typed messages: the language is recognised from the script or common words, and short
    replies such as "ok" keep the conversation's language.
  - Each reply sentence is spoken by a voice for its own language: Hindi (`hf_alpha`,
    `hf_beta`), Spanish, French, Italian, Portuguese, Japanese and Chinese. Languages with no
    Kokoro voice (German, for example) are answered in text, with a one-time notice.
  - Hindi built-in commands, in Devanagari or romanised: "Chrome kholo", "गाना रोको",
    "आवाज़ बढ़ाओ", "VLC install karo", "याद रखना कि …". Chiku acknowledges them in Hindi.
- New settings: `stt.preferred_languages`, `tts.voices` (voice per language), and `stt.language`
  `auto`. A Languages tab in Settings has a Hindi voice test.
- Discord Rich Presence (opt-in, Settings → Privacy): "Chatting with Chiku · Local AI voice
  assistant" on your Discord profile, with **Get Chiku** and **Releases** buttons linking to
  the GitHub repository. Fixed text only; nothing from conversations, memory, apps or files.
  It talks to the Discord app through its local IPC pipe (no new dependency, no network
  calls from Chiku), retries quietly while Discord isn't running, and clears the status when
  turned off or when Chiku quits.
- Pro workspace: three panes.
  - **Left:** pages (Chat, Profile, Apps, Memory, Activity, Settings) and a list of chats.
  - **Centre:** the chat or the current page.
  - **Right:** the live mascot, voice level and component status, the sources of the last
    answer and recent actions. Ctrl+. toggles it; it hides itself below 1060 px.
- Conversations: several chats (Ctrl+N for a new one; right-click to delete), each with its
  own model context. They are kept in memory only, unless Settings → Privacy → "Keep
  conversation history" is ticked (`conversations.keep_history`, off by default); unticking
  it deletes `conversations.json`. A reopened chat gives the model back its text, without
  earlier tool calls.
- Command palette (Ctrl+K): pages, actions (new chat, mic, stop, notch, card, wake word,
  theme, side panel, app updates, quit), every Settings tab, past chats, and "Open <app>",
  which goes through the normal permission-checked command path.
- Profile page:
  - Name and pronunciation (with a "Hear it" button), and an optional picture.
  - Interests (`persona.interests`, used in the prompt), answer length, tone, reply
    language, and English and Hindi voices.
  - Memories grouped by category.
  - Usage counted from the local action log.
  - Export my data (JSON) and Delete my data.
- Apps page: installed apps (filter), available updates and "Get apps" search, all through
  winget. Install, update and uninstall run through the same tools as voice requests, so
  each is confirmed and logged.
- Notch: minimised Chiku as a pill hanging from the top edge of the screen, with the mascot,
  live state, level meter and mic. Hover or click to see the last reply and Stop / Open /
  Card / Hide. Drag it anywhere, including to another monitor; on release it snaps to the top
  edge of the screen under the cursor. The position is remembered per screen
  (`ui.notch_positions`), and if that monitor is unplugged the notch moves to the primary
  screen. The notch is the default minimised style (`ui.mini_mode`); the floating card
  remains as an option. Ctrl+Shift+Space switches between the full window and the minimised
  style, and the tray menu offers both styles.
- Background mode and wake word (opt-in, Settings → Background, one ~75 MB download).
  - While the mic is off, say "Hey Chiku" or "Wake up". Chiku chimes and listens; "Hey Chiku,
    what time is it?" in one breath is answered directly. After a quiet follow-up period
    (`wake.follow_up_s`, 20 s by default) she goes back to waiting.
  - Short speech segments found by voice activity detection are checked by Whisper `tiny.en`
    on 2 CPU threads. Transcripts are compared with the wake phrases and discarded: never
    shown, logged, stored or sent.
  - The tray icon shows a green dot while the mic is open for the wake word, and its menu
    switches the wake word on or off. Listening pauses while the screen is locked.
  - With the wake word on, closing the window keeps Chiku in the tray; "Quit Chiku" in the
    tray menu exits. If she wakes while hidden, the notch appears and hides again.
  - "Start Chiku in the background when I sign in" adds a per-user `Run` registry value that
    launches `Chiku.exe --background` (tray only). The uninstaller removes it.

### Fixed

- winget commands with large output (`winget list` on a PC with many apps, or many available
  updates) never finished. The output was only read after winget exited, so winget blocked
  on a full pipe until the 2-minute timeout. It is now read while waiting: 220 installed
  apps list in about 1 s.

### Changed

- The system prompt asks for a reply in the user's language, and each user message names the
  language to reply in. The prompt itself is unchanged between turns, so it stays cached.
- The Whisper hallucination filter now keeps non-Latin text. Previously, any Hindi transcript
  would have been discarded as empty.
- Sentence splitting for speech recognises the Hindi full stop (।).

## [0.5.2] - 2026-10-28

### Added
- **App management with winget.** Say "install VLC", "uninstall Zoom", "update VS Code" or
  "check for updates". Chiku can also look apps up (`find_package`). Every install, uninstall
  or update shows a confirmation with the exact package (name, id, version, publisher,
  source). Only the official `winget` and `msstore` sources are used; nothing is downloaded
  from arbitrary websites. Per-user installs are tried first, and machine-wide installers get
  Windows' own UAC prompt. Results are verified with `winget list`, and the app list refreshes
  so "open VLC" works right after installing.
- **New UI.**
  - Chat-app layout: a sidebar (Chat, Memory, Activity, Settings as pages instead of
    pop-ups) and chat bubbles with timestamps and provenance.
  - Animated mascot header with a live level meter (mint for your voice, yellow for hers).
  - Compact mode: a small, always-on-top, draggable Chiku. Toggle with Ctrl+Shift+Space, or
    choose "Start in compact mode".
  - Tray icon.
  - Warm light and dark themes based on the mascot; the default follows Windows.

### Changed
- UI code split into a view-model (`ui/viewmodel.py`) and components (`ui/components/`), with
  offscreen UI tests.

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

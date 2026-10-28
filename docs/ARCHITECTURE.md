# Architecture

## Technology choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ (tested on 3.14) | Best ecosystem for local speech and LLM inference. Only Python 3.14 was installed, and every dependency has 3.14 wheels. |
| Desktop UI | PySide6 (Qt 6), native widgets | A real Windows app with no browser runtime. Same process as the engine, separated by an event interface. The .NET SDK isn't installed, so WinUI was not an option without more installs. |
| Audio I/O | sounddevice (PortAudio): WASAPI preferred, MME default | Low-latency callbacks, device selection, and Windows-side rate conversion. |
| VAD | Silero VAD v5 (ONNX via onnxruntime) | Accurate, about 1 ms per 32 ms frame on CPU, no PyTorch. An energy-based detector is the fallback. |
| Speech-to-text | faster-whisper (CTranslate2), `small.en` | Offline, MIT, accurate. CUDA float16 on NVIDIA, int8 on CPU as fallback. |
| Text-to-speech | Kokoro-82M (ONNX) via kokoro-onnx, voice `af_heart` | Natural female voices, Apache-2.0, faster than real time on CPU. |
| LLM | Ollama, `qwen3:4b-instruct-2507-q4_K_M` | Local, tool calling, streaming. Fits 8 GB VRAM alongside Whisper. The instruct build answers without hidden reasoning. |
| Settings | pydantic models, JSON in `%APPDATA%\Chiku` | Typed and validated. A corrupt file falls back to defaults. |
| Secrets | `keyring` (Windows Credential Manager) | No keys in files. Unused in Phase 1. |

### Engines considered and rejected

- **Windows SAPI / OneCore voices.** These are robotic compared with Kokoro, and the natural
  "Narrator" voices aren't available to third-party apps.
- **Piper TTS.** Good and fast, but its female English voices sounded less natural than
  Kokoro's in practice, and newer releases are GPL.
- **Windows.Media.SpeechRecognition.** Needs WinRT plumbing and an online-speech privacy
  setting, and is less accurate than Whisper.
- **Whisper on CPU only.** I measured 6–8 s per utterance with 8 threads and 2.2 s with 16
  threads. That's too slow for conversation, so the GPU path became the default whenever
  available.
- **`qwen3:4b` (the default Ollama tag).** It now resolves to the always-thinking 2507 build,
  which ignores `think:false` and emits raw reasoning. The provider detects such models with a
  one-shot probe and asks Ollama to keep the reasoning in a separate field, which is never
  shown or spoken. The instruct build is the default.

## Layout

```text
src/chiku/
  __main__.py        entry point: GUI, or diagnostics flags
  bootstrap.py       wires real components from settings
  assistant.py       conversation engine (UI-agnostic, event-driven)
  config.py          typed settings, load/save, loopback check
  persona.py         persona settings + system prompt
  intents.py         deterministic fast path ("open X")
  text.py            sentence chunking, <think> filtering, speech cleanup
  cancel.py          CancelToken shared by every long operation
  audit.py           JSONL action log
  credentials.py     Windows Credential Manager wrapper
  hardware.py        CPU/GPU/RAM detection + recommendations
  models.py          model inventory, checksummed resumable downloads
  diagnostics.py     --check, --say, --mic-test, --chat, --list-apps, --open-app
  audio/  devices.py capture.py vad.py playback.py
  stt/    whisper.py
  tts/    kokoro.py
  llm/    base.py (provider interface)  ollama.py
  tools/  base.py (Tool contract)  registry.py (executor)  apps.py  web.py  system_info.py
  ui/     app.py main_window.py settings_dialog.py waveform.py theme.py
```

## Data flow

```text
 mic ──► MicCapture ──► listener thread ──► SileroVAD ──► UtteranceSegmenter
 (16 kHz mono, 32 ms frames)       │                          │ SpeechEnded(audio)
                                   │ barge-in detector        ▼
                                   │ (while speaking)    turn queue ──► worker thread
                                   ▼                                       │
                            interrupt() ◄──────────────────────────────────┤
                                                                           ▼
                                                    WhisperSTT ─► text ─► fast path? ─► ToolExecutor
                                                                           │ no
                                                                           ▼
                                                   OllamaProvider.stream_chat (tools, history)
                                                           │ text deltas          │ tool calls
                                                           ▼                      ▼
                                              ThinkFilter ─► SentenceChunker   ToolExecutor
                                                           │                  (validate, permit,
                                                           ▼                   timeout, audit)
                                                 speaker thread: Kokoro ─► AudioPlayer ─► speakers
```

### Threads

- **Qt main thread.** The UI only. It receives `Event`s through a queued signal and polls
  levels at 30 Hz.
- **listener.** Reads mic frames, runs VAD, segments utterances, detects barge-in, and
  restarts the mic after failures (3 retries, then mic off plus an error state).
- **worker.** Runs one turn at a time: STT, then either the fast path or the LLM with up to
  4 tool rounds.
- **speaker.** Starts once per turn. It synthesizes each sentence as soon as the chunker
  releases it, so speech begins while the model is still generating.
- **tool pool.** Every tool runs in a two-thread pool with a deadline and cooperative
  cancellation.

### State machine (what the UI shows)

`loading → idle (mic off) ⇄ listening → hearing → thinking → speaking → listening`, plus
`error` (for example, the microphone failed). These states are emitted by the engine from real
events. The waveform draws real RMS from the running capture or playback stream and goes flat
when no stream is active.

### Cancellation and barge-in

`interrupt()` cancels the turn's `CancelToken` and clears the player buffer. Playback stops
within one audio block (about 40 ms). The Ollama stream loop sees the token, closes the HTTP
connection, and Ollama stops generating. A sentence that is mid-synthesis gets discarded. The
interrupted reply goes into history marked `[interrupted]`, so the model knows what the user
actually heard. If the user starts talking while Chiku is still thinking, the in-flight reply is
dropped. Their earlier words stay in history, so the next turn sees everything.

### Provider interface

`LLMProvider` (`llm/base.py`) defines four things:

- `is_local`
- `label`, which is shown on every reply
- `status()`
- `stream_chat(messages, tools, cancel)`, which yields `StreamChunk(text, tool_calls, done)`

Errors are `LLMUnavailable` (not running, or model missing) and `LLMError`. Adding a cloud
adapter means implementing this class with `is_local = False` and an honest label. The engine
never needs to change.

### Tool contract

Each tool (`tools/base.py`) declares:

- a unique `name`
- a `description`
- an `Input` pydantic model, which supplies the typed JSON schema and validation and forbids
  extra fields
- a default `permission`, plus an optional per-argument `permission_for()`
- `timeout_s`
- `run(args, ctx)`, which returns a structured `ToolResult(ok, message, data, error_code)`

`ToolExecutor` is the only path to execution. It looks up the tool, validates input, checks
permission (auto, confirm or deny), asks the UI for confirmation if needed, runs the tool with a
deadline and cancellation, converts exceptions into results, and writes an audit record. Results
go back to the model as JSON tool messages, and the system prompt marks them as untrusted data.

### Web research (Phase 3)

```text
user text ─► research_query()? ──yes──► web_search (Brave) ─► read_web_page (top result)
     │              │                         │                      │ fails? drop from context
     │ links? ──────┴──► read_web_page        └──── results + page text as tool messages ─► LLM
     └─► otherwise the LLM may still call web_search / read_web_page itself
```

- `web/search.py`: the `SearchProvider` protocol, implemented by `BraveSearch`. It sends only
  the query, with the key in the `X-Subscription-Token` header. Results are stripped of HTML and
  limited to http(s) URLs.
- `web/fetch.py`: `fetch_page`. Every redirect hop is resolved and must be a public (global) IP.
  Max 5 redirects. HTML or plain text only. 2 MB body cap and 6,000-character text cap. It
  respects 401/403/429/451 and uses an honest User-Agent. Text extraction uses the stdlib
  `HTMLParser`, prefers `<main>`/`<article>`, and drops scripts, styles and navigation.
- `tools/research.py`: `WebSearchTool` and `ReadWebPageTool`, plus `SourceSession`. URLs
  returned by searches, or typed by the user, can be read without a prompt. Any other URL (for
  example, one suggested by page content) needs a confirmation.
- **Deterministic routing** (`intents.research_query`, `Assistant._prefetch_*`). Qwen3-4B
  rarely calls the research tools on its own, even when they're offered. Three kinds of request
  are therefore handled by running the tools *before* the model answers, through the same
  executor: explicit "search/look up X", freshness questions (weather, news, latest, prices,
  scores, "who won", years), and messages that contain links. Questions about the local computer
  are excluded. Failed optional page reads are removed from the model's context, because they
  made the small model ignore good snippets.
- Sources from any `ok` tool result's `data.sources` are collected per turn and sent with the
  final `Message`. The UI renders them as numbered links. Clicking one goes through the
  `open_url` tool, so its permissions apply.
- Long tool outputs (page or file text) are shown in full to the model for the current turn
  only. History keeps a copy trimmed to 1,500 characters.

### Files (Phase 3)

`tools/files.py` resolves every path through a `FileScope` built from the Windows *known
folders* (`SHGetKnownFolderPath`, so OneDrive redirection is honoured). It accepts folder
aliases ("my documents"), absolute paths, and bare names that are unique across the allowed
roots. It rejects:

- UNC and device paths
- alternate data streams
- reserved names (`CON`, `NUL`, …)
- anything that resolves outside the roots after following `..`, symlinks and junctions

The roots themselves can't be moved or recycled. Tools:

- `find_files`: bounded walk that skips `node_modules`, `.git`, `AppData` and similar
- `list_folder`
- `open_file`: confirms programs and scripts
- `read_text_file`: text extensions only, 2 MB max
- `create_folder`
- `move_or_rename`: confirm, never overwrite
- `move_to_recycle_bin`: confirm, `SHFileOperationW` with `FOF_ALLOWUNDO | FOF_WANTNUKEWARNING`

Every change is verified afterwards.

### Apps, windows and media (Phase 3)

`tools/windows_ctl.py` puts all Win32 calls behind a `WindowBackend` protocol, so they're
testable without a desktop. It works only with normal app windows: it skips owned, tool,
cloaked and Chiku's own windows. Matching uses the app catalog's exe name, then the process
name, then title words.

- **Switch:** `ShowWindow(SW_RESTORE)` + `SetForegroundWindow`, verified with
  `GetForegroundWindow`. If focus is blocked, it falls back to minimise and restore.
- **Close:** posts `WM_CLOSE`, then verifies within 5 s and reports "may be asking to save" if
  the window stays.
- **Media:** sends media virtual keys.

The fast path covers "switch to X" and "close X" only when a matching window exists, and
handles media phrases. "stop" is deliberately excluded, because that word interrupts Chiku.

### App control and messaging

`automation/keyboard.py` sends keys with `SendInput`; text goes as `KEYEVENTF_UNICODE`
characters, so any script types without the clipboard. `automation/uia.py` wraps Windows UI
Automation through comtypes: list a window's named controls, press one by name (Invoke,
Toggle, Select or Expand pattern) and report the focused control. Both sit behind protocols
so tests run without a desktop.

`tools/app_control.py` builds five tools on a shared `Desktop` (window backend, keyboard,
UI Automation, app catalog): `read_app` (automatic), `press_keys`, `type_text`,
`click_in_app` (each confirmed) and `send_message`. Windows of shells, terminals and admin
tools are refused by `permission_for`.

`send_message` is automatic to start because it only opens a chat; sending is a confirmation
asked in the middle of the run through `ToolContext.confirm`, and the audit entry records the
answer. Its steps:

1. **Discord:** find or launch the desktop app, focus it, Ctrl+K, check the quick switcher has
   the focus, type `@name` (people only, so a direct message beats a group containing the
   name), Enter, and wait up to 6 s for the window title to name the person; otherwise try the
   plain name (groups and channels).
2. **WhatsApp:** find a browser window titled WhatsApp, else select a WhatsApp tab through UI
   Automation, else open web.whatsapp.com; Ctrl+Alt+/, check the search has the focus, type
   the name, Enter.
3. Check the message box has the focus, type the text, ask, check the focus again, Enter.

Every "check the focus" stops the run with `focus_check_failed`, typing nothing, when the
focused control isn't the expected one.

### Memory (Phase 4)

`memory.py` holds `MemoryStore`, a SQLite file at `%LOCALAPPDATA%\Chiku\memory.db` with one
table, `memories(id, text, category, created, updated, source)`. It guards with an `RLock`
because tools run in a thread pool. It rejects:

- secrets, via `sensitive_reason`: password, PIN or OTP phrases, Luhn-valid card numbers,
  API-token prefixes, PAN, Aadhaar and SSN patterns
- empty text and anything over 300 characters
- case-insensitive duplicates, which return the existing entry

`clear()` runs `VACUUM` so deleted text doesn't linger in the file.

- **Prompt.** `prompt_section()` lists up to `memory.max_in_prompt` (40) entries with their
  `[id]`. If the provider isn't local and `share_with_cloud` is off, the memory section and the
  memory tools are left out entirely.
- **Consent.** `RememberTool` is AUTO only when your current message asks for it ("remember",
  "don't forget", "keep in mind", "note that"); otherwise it needs confirmation. The assistant
  exposes `current_user_text()` for this check.
- **Forgetting.** `ForgetTool` is AUTO only when you asked to forget and the matched entry is
  just that fact (`_close_match`). An entry that bundles more facts is shown in a confirmation
  first.
- **Fast paths.** "remember (that) …" and "forget (that/about) …" bypass the model. Leading
  filler such as "also", "btw" or "oh" is tolerated. First-person phrasing becomes a note ("I'm
  vegetarian and I live in Pune" becomes "User is vegetarian" and "User lives in Pune", stored
  separately).

### Providers (Phase 4)

`make_llm()` picks the provider from `llm.provider`:

- `ollama`: `OllamaProvider`
- `openai_compat`: `OpenAICompatProvider`

`OpenAICompatProvider` targets `/v1/chat/completions`. It streams SSE, rebuilds tool-call
argument fragments by index, and sends `tool_call_id` on tool messages (the assistant now gives
every call an id). A non-loopback base URL raises unless `allow_cloud` is set. The label is
then `CLOUD · model (host)`. The optional key lives in Credential Manager
(`openai_compat_api_key`). A refused configuration becomes an `UnavailableProvider`, so the app
still starts and explains why.

### Model management (Phase 4)

`llm/manage.py` provides `OllamaManager` with `installed()` (`/api/tags`), `loaded()` (`/api/ps`
VRAM), `capabilities()` (`/api/show`), `pull()` and `delete()` (`/api/delete`).

`pull()` streams `/api/pull` progress, cancellably. As soon as the size is known, it aborts if
the download would leave less than 2 GB free on the Ollama models drive. Model names are
validated before any request.

`ui/models_dialog.py` shows all of this, switches models via `apply_settings`, and
downloads/selects Whisper sizes.

### Tool registry controls and action history (Phase 4)

`settings.tools.disabled` hides tools from the model's schema list. `ToolExecutor` refuses them
(`tool_disabled`), so a disabled tool can't run even through a fast path or a stale model call.
`ToolRegistry.all()` drives the per-tool switches in Settings → Tools. To add a new tool:
subclass `Tool` (name, description, pydantic `Input`, permission, timeout, `run`) and register
it in `bootstrap.build_registry`. Nothing else changes.

`ui/activity_dialog.py` renders the JSONL audit log (`AuditLog.tail`). `AuditLog.clear()`
deletes it after confirmation.

### Branding, onboarding and packaging (v0.5)

**Brand assets.** The originals in `assets/branding/` are never modified, and a test pins their
SHA-256.

| Constant | File | Type |
|---|---|---|
| `CHIKU_MASCOT_TRANSPARENT` | `chiku-transparent.png` | RGBA, 500×500, transparent corners |
| `CHIKU_MASCOT_BACKGROUND` | `chiku-background.jpg` | RGB, 800×800, grey #CBCBCB background |

`scripts/build_assets.py` derives:

- the multi-size `.ico` (16–256 px, transparency preserved)
- a 256 px avatar
- a 480 px welcome image
- letter-boxed installer bitmaps (100% and 200% DPI)

UI code gets every path through `chiku.branding.asset()`.

**Mascot widget.** `ui/mascot.py` animates only scale, rotation, offset and opacity. It is
driven by the engine's real `State` and the live capture or playback RMS.

**First-run setup.** `ui/onboarding.py` reads CPU, RAM and every display adapter (WMI) plus
NVIDIA VRAM (nvidia-smi). `hardware.recommend_setup()` then picks the Whisper size and device
and the LLM, for NVIDIA, other-GPU and CPU-only PCs alike. Every download is opt-in and shows
its size. On NVIDIA PCs, `gpu_pack.py` installs a checksum-verified cuBLAS/cuDNN DLL pack.

**Packaging.** `packaging/chiku.spec` is a PyInstaller onedir build containing `Chiku.exe`
(GUI) and `chiku-cli.exe` (diagnostics). Unused Qt modules are excluded and models are not
bundled. `packaging/chiku.iss` is the Inno Setup installer:

- per-user by default
- AppMutex for safe upgrades, plus a clean `_internal` directory on upgrade
- asks whether to keep personal data on uninstall

`packaging/build.ps1` builds both. It runs a smoke test before packaging (`chiku-cli
--version-info` must load every native component); this caught a missing phonemizer data file.
`packaging/verify_install.ps1` checks install, launch, upgrade and uninstall end to end.

### App discovery and verification

Apps are discovered from two documented sources: Start Menu `.lnk` shortcuts (with resolved
targets) and `Get-StartApps` (which includes Store/UWP AppUserModelIDs). That takes about 3 s
for 187 apps on the test machine, and the result is cached for 10 minutes. Launching uses
ShellExecute on the shortcut, or `explorer.exe shell:AppsFolder\<AUMID>`. Verification compares
process and window snapshots taken before and after the launch, polling for 8 s. It looks for a
new process with the target exe name, or a new visible window owned by that exe or titled with
the app's name. The model only ever supplies an app *name*. The executable always comes from the
catalog.

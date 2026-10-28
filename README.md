<p align="center">
  <img src="assets/branding/chiku-background.jpg" alt="Chiku, a yellow chick in a mint beanie" width="260">
</p>

<h1 align="center">Chiku</h1>

<p align="center">A local-first, female-voiced AI assistant for Windows. Open source, private by default.</p>

<p align="center">
  <a href="https://github.com/Intruder-tech/Chiku/releases">Download</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="docs/SECURITY.md">Privacy &amp; security</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

## Install (Windows 10/11)

1. Download `Chiku-Setup-<version>.exe` from
   [Releases](https://github.com/Intruder-tech/Chiku/releases). Check it against the `.sha256`
   file published next to it.
2. Run it. It installs for your user account only and needs no admin rights. You can choose to
   add a desktop shortcut.
3. Start Chiku. The first-run setup checks your PC's CPU, GPU and memory, recommends models
   that fit, and downloads them only after you say yes. Typical first download: about 0.9 GB for
   voice and speech, plus 1.3–2.5 GB for the language model.
4. Install [Ollama](https://ollama.com/download) when the setup asks. It runs the language model
   locally.

Upgrading: run a newer installer; your settings, memories and models are kept. Uninstalling
asks whether you also want to delete your personal data.

**Not yet tested:** the installer on PCs other than the developer's laptop (i7-13700HX,
RTX 5050, 16 GB RAM), and on AMD or Intel-only graphics. Please report how it goes.

## About

Chiku is a local-first, female-voiced personal AI assistant for Windows 11. You talk to her,
she answers out loud, and she can safely do things on your laptop: open and switch apps, control
music and volume, find and organise files, search the web and read pages with sources. Speech
recognition, the voice and the language model all run on your own computer; only web searches
(the query words) leave it.

Status: **v0.5.0. Phases 1–5 are implemented. The Windows installer is built and was verified on the developer's PC.** Most of Phase 2 is done.

- Phase 1: voice proof of concept.
- Phase 2 (mostly done): multi-turn context, streaming replies, barge-in, VAD, persona settings.
- Phase 3: web research with sources, file operations, app/window and media control.
- Phase 4: memory you control, provider choice, model management, tool switches, action
  history, and privacy controls.

See [docs/TESTING.md](docs/TESTING.md) for what was verified and how, and "Known limitations"
below.

## What it does today

- Live, hands-free conversation: mic → voice activity detection → speech-to-text → local LLM
  (streamed) → female text-to-speech, sentence by sentence while the model is still writing.
- Interrupt her by talking (barge-in), with the Stop button, or with Esc.
- "Chiku, open Chrome" → she acknowledges, launches the app, **verifies** a new process or
  window appeared, and reports success or failure.
- **Web research.** Ask about something current ("will it rain tomorrow?", "latest news on
  …", "search for …") and Chiku searches Brave, reads the best page, and answers from the
  sources. She keeps what the sources say separate from her own inference, and the sources
  appear as clickable links under the reply. Give her a link and she reads it.
- **Files**, limited to Desktop, Documents, Downloads, Pictures, Music and Videos:
  - find, list, open, and read text files to summarise them
  - create folders
  - move and rename, which asks you first and never overwrites
  - delete, which asks you first and only ever goes to the Recycle Bin
- **Apps and media.** Switch to an open app, close one (asks first, using the app's normal
  close so it can offer to save), list open windows. Play/pause, next/previous track, volume
  up/down, mute. Common phrases ("pause the music", "switch to Chrome", "turn it down") run
  instantly without the AI model.
- Other tools: open app, find app, open URL (approved domains open directly, others ask), read
  system info (battery, CPU, RAM, disk, GPU, time).
- Every reply is labelled with where it was generated (e.g. `local · qwen3:4b-instruct…
  (Ollama)`), plus "with web sources" when it used the web.
- Settings for microphone/speaker, voice and pace, model, persona, web search, files,
  permissions and privacy.
- Every tool action is written to a local audit log, which you can browse in **Activity**.
- **Memory you control.** "Remember that I'm vegetarian" saves a note immediately, because you
  asked. Anything the model wants to remember on its own needs your OK. Open **Memory** to see,
  add, edit, delete, export or wipe everything. Passwords, PINs, OTPs, card numbers, API keys
  and ID numbers are refused. Memories never leave your PC: they are left out of requests to a
  cloud provider unless you allow it.
- **Providers and models.** Ollama is the default. Any OpenAI-compatible server (LM Studio,
  llama.cpp `llama-server`, vLLM, Jan) also works. A cloud API works only if you tick "Allow a
  cloud provider" and accept the disclosure; its replies are labelled CLOUD. **Settings → AI
  model → Manage models** lists installed models with size, quantization, tool support and VRAM
  use. You can download (with progress and a disk-space check), delete or switch models there,
  and pick the Whisper size.
- **Install and update apps (winget).** "Install VLC", "uninstall Zoom", "check for
  updates". You confirm every change in a dialog showing the exact package. Only official
  winget and Microsoft Store sources are used.
- **Chat-app layout and compact mode.** Sidebar pages, chat bubbles with timestamps, and a
  floating always-on-top Chiku (Ctrl+Shift+Space). Warm light and dark themes follow Windows.
- **Tool switches.** Settings → Tools turns any single ability off; that tool is hidden from the
  AI and refused.

### Web search setup (one time)

Get a free API key at <https://api-search.brave.com> (the free tier allows about 2,000
queries a month). Then either:

- paste it in **Settings → Web → Store key**, or
- run `.\.venv\Scripts\python.exe -m chiku --set-search-key`.

The key is kept in Windows Credential Manager, never in a file. Without a key, Chiku still reads
links you give her, and she tells you how to add the key when you ask something current.

## Components that get installed

| Component | Size | Purpose | License |
|---|---|---|---|
| Python packages (`pip install -e .`) | ~600 MB | app, Qt UI, ONNX runtime, CTranslate2 | see [docs/SECURITY.md](docs/SECURITY.md#dependency-and-license-review) |
| `[cuda]` extra: cuBLAS 12 + cuDNN 9 (optional) | ~1.2 GB download | GPU speech recognition | NVIDIA proprietary (redistributable) |
| Ollama | ~1 GB | local LLM runtime | MIT |
| `qwen3:4b-instruct-2507-q4_K_M` | 2.5 GB disk, ~3.9 GB VRAM at 8k context | language model | Apache-2.0 |
| Kokoro-82M + voices | 338 MB | female voice (CPU) | Apache-2.0 |
| Whisper `small.en` (CTranslate2) | 484 MB | speech-to-text | MIT |
| Silero VAD v5 | 2 MB | voice activity detection | MIT |

Models are stored in `%LOCALAPPDATA%\Chiku\models` (not in this folder, so OneDrive doesn't sync
them).

## Requirements

- Windows 11, a microphone, speakers or (better) headphones.
- Python 3.11+ (developed and tested on 3.14.0).
- ~6 GB free disk space.
- Optional: an NVIDIA GPU (tested on RTX 5050 Laptop, 8 GB) for fast speech recognition.
  Without it, speech recognition runs on the CPU and is much slower (see benchmarks).

## Install

From this folder in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# Optional but strongly recommended with an NVIDIA GPU:
.\.venv\Scripts\python.exe -m pip install -e ".[cuda]"

# Local language model runtime + model
winget install --id Ollama.Ollama -e
ollama pull qwen3:4b-instruct-2507-q4_K_M

# Voice, speech recognition and VAD models (shows sizes and asks first)
.\.venv\Scripts\python.exe -m chiku --download-models

# Verify everything (hardware, devices, models, Ollama, voice -> speech round trip)
.\.venv\Scripts\python.exe -m chiku --check
```

## Run

```powershell
.\.venv\Scripts\pythonw.exe -m chiku      # desktop app, no console window
.\.venv\Scripts\python.exe -m chiku       # same, with a console for errors
```

Turn on the **Mic** button (Ctrl+M) and talk. Esc or **Stop** interrupts. You can also type.

### Command-line tools

| Command | What it does |
|---|---|
| `python -m chiku --check` | Hardware, audio devices, models, Ollama, TTS→STT round trip |
| `python -m chiku --download-models [--yes]` | Download missing models (asks first) |
| `python -m chiku --say "text"` | Speak through the configured speaker |
| `python -m chiku --mic-test 5` | Record 5 s, show levels/VAD, transcribe |
| `python -m chiku --chat "text" [--mute]` | One full assistant turn from text |
| `python -m chiku --list-apps [query]` | Show/search the installed-app catalog |
| `python -m chiku --open-app NAME` | Open an app through the permissioned tool path |
| `python -m chiku --set-search-key` | Store/remove the Brave Search key (hidden input) |
| `python -m chiku --search "query"` | One web search (sends the query to Brave) |
| `python -m chiku --read-page URL` | Fetch a page and show the extracted text |
| `python -m chiku --find-files NAME` | Search your user folders by file name |
| `python -m chiku --windows` | List open app windows |
| `python -m chiku --memory` | List what Chiku remembers |
| `python -m chiku --models` | List installed Ollama models (size, capabilities, VRAM) |

## Configuration

Settings are edited in the app (Settings, Ctrl+,) and saved to `%APPDATA%\Chiku\settings.json`.
[config/settings.example.json](config/settings.example.json) shows every option with defaults.
Notable ones:

- `stt.device`: `auto` (GPU if the CUDA libraries are installed, else CPU), `cuda`, `cpu`.
- `tts.voice`: any Kokoro female voice, e.g. `af_heart` (default), `af_bella`, `bf_emma`, `hf_alpha`.
- `llm.model`: any Ollama model that supports tools. Instruct (non-reasoning) models answer
  faster; "thinking" models work but start speaking later.
- `llm.ollama_url`: must be on this computer unless `allow_remote_ollama` is true.
- `audio.barge_in`: interrupt by talking. Reliable with headphones; on laptop speakers Chiku can
  hear herself, so you may prefer Esc/Stop.
- `persona.*`: your name, tone, reply length, language, pronunciation fixes, extra preferences.
- `permissions.*`: auto-open apps, always confirm sensitive apps, URL domains that open without
  asking.

No API keys are needed. If cloud providers are added later, keys go to Windows Credential
Manager via `chiku.credentials`, never into files. Set `CHIKU_HOME` to keep all data in one
folder (portable mode).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest                         # 283 unit tests, no hardware needed
.\.venv\Scripts\python.exe -m pytest -m "models or ollama or system"   # real models, Ollama, Recycle Bin
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe
```

Manual, hardware-dependent checks are listed in [docs/TESTING.md](docs/TESTING.md).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, threads, data flow, technology choices
- [docs/SECURITY.md](docs/SECURITY.md): privacy, permissions, threat model, dependency licenses
- [docs/TESTING.md](docs/TESTING.md): automated and manual verification, with results
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md): measured latency/memory, hardware and method
- [docs/OPENJARVIS.md](docs/OPENJARVIS.md): evaluation of the OpenJarvis reference project

## Known limitations

- **No acoustic echo cancellation.** With laptop speakers, Chiku's own voice can trigger
  barge-in or be picked up as speech. Barge-in uses a stricter detector while she talks, but
  headphones are recommended. Turning barge-in off makes her ignore the mic while speaking.
- English speech recognition only (`*.en` Whisper models). Kokoro has Hindi and other female
  voices, but the persona/STT pipeline is English-first.
- The first GPU load of Whisper on a new driver/GPU can take ~1 minute (CUDA kernel compile);
  later starts take seconds.
- App launch verification looks for a new matching process or window within 8 s. Apps that
  start very slowly, or that only hand off to an already running instance without a new window,
  may be reported as "already running" or "couldn't confirm".
- Confirmation prompts for sensitive actions are on-screen dialogs, not voice confirmations.
  For deletions the model usually also asks you out loud first, so you confirm twice.
- The small local model rarely decides on its own to search or read pages. So Chiku routes
  those requests herself in three cases: questions that clearly need fresh information, explicit
  "search/look up" requests, and messages containing links. Other questions get the model's own
  (possibly outdated) knowledge.
- Some sites refuse automated reading (Wikipedia/Wikimedia return HTTP 403 to non-browser
  clients). Chiku respects that and answers from the search snippet instead of disguising
  itself.
- Media keys go to whichever app Windows routes them to; Chiku can't confirm a player reacted.
- Memory is a simple list placed in the prompt (up to 40 items; older ones are reachable via
  `search_memory`). There is no semantic or embedding search yet.
- "Remember …" with unusual phrasing may go to the model. A 4B model sometimes *says* it will
  remember without calling the tool; check **Memory** if it matters.
- Not implemented yet: PDF and Word document reading, global hotkey, installer/packaging,
  Phase 5 optimisation.

## Development

Start from source with the development install in [CONTRIBUTING.md](CONTRIBUTING.md). The
desktop stack decision is recorded in [docs/ADR-001-desktop-stack.md](docs/ADR-001-desktop-stack.md).

To build the installer, run `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`.
This runs PyInstaller and then Inno Setup, and writes `packaging\output\`.

To measure performance on your machine, run `python -m chiku --benchmark`; see
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

## License

- **Code:** [GPL-3.0-or-later](LICENSE). Third-party components and models are listed in
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **Chiku mascot artwork:** all rights reserved; see
  [assets/branding/LICENSE.md](assets/branding/LICENSE.md). Forks must use their own branding.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Ollama isn't reachable" | Start Ollama (Start menu) or run `ollama serve`. |
| "Model … isn't installed" | `ollama pull <model>` or pick an installed model in Settings → AI model. |
| "Speech model isn't downloaded" | `python -m chiku --download-models` |
| Mic button turns itself off | The microphone failed 4 times in a row (unplugged / in use / blocked by Windows privacy settings: Settings → Privacy & security → Microphone → allow desktop apps). |
| She interrupts herself | Use headphones, or turn off "Let me interrupt Chiku by talking". |
| Slow replies after speaking | Check `--check` shows speech recognition "on cuda". If CPU, install the `[cuda]` extra. |
| Logs | `%LOCALAPPDATA%\Chiku\logs\chiku.log` (no transcripts are logged). |
| Action history | `%LOCALAPPDATA%\Chiku\audit\actions.jsonl` |

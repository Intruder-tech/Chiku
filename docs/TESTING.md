# Testing and verification

## Automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest                         # unit + engine tests, no hardware
.\.venv\Scripts\python.exe -m pytest -m "models or ollama"   # real models and Ollama
.\.venv\Scripts\ruff.exe check src tests                     # lint (pycodestyle, bugbear, bandit subset...)
.\.venv\Scripts\mypy.exe                                     # types (pydantic plugin)
```

| Suite | What it covers |
|---|---|
| `test_assistant.py` | The full engine with fakes. Text turn, streamed speech, fast-path app open plus verification, LLM fallback for unknown apps, failed-launch reporting, LLM tool round trip, sensitive-app confirmation (denied), invalid tool arguments returned to the model, LLM unavailable (spoken plus notice), TTS failure (text still shown), interrupt during generation and during playback, voice turn from the mic through VAD and STT, empty transcript, barge-in on and off, mic failure leading to the error state, mic recovery, history trimming, pronunciations, level meters going flat when streams are inactive. |
| `test_ollama.py` | Ollama adapter against a mock HTTP server. Streaming, stable options, tool-call parsing, tool message wire format, missing model, connection refused (with retry), cancellation mid-stream, server errors, refusal of remote URLs, think handling for hybrid and always-thinking models. |
| `test_tools.py` | Registry and executor. Name rules, schema shape, unknown tool, invalid input (missing, extra, non-dict), confirm and deny paths, DENY, exceptions, timeout, cancellation, audit records and truncation. |
| `test_apps.py` | Discovery parsing (junk, script and injection filtering), alias search, sensitive-app detection, open success, not found, not verified, already running, OSError, loader failure. |
| `test_web.py` | URL validation (schemes, credentials, IDN, length), domain allow-list, permission levels, opening. |
| `test_text.py`, `test_vad.py`, `test_intents.py`, `test_config.py` | Sentence chunking, think filtering, speech cleanup, segmenter, barge detector, energy VAD, resampler, command parsing, settings round trip and corruption, loopback detection, system prompt, credential store. |
| `test_integration.py` (opt-in) | Real Kokoro → Silero → Whisper round trip. Real Ollama stream. Full spoken turn through the real engine: Kokoro speech in, Silero, Whisper on CUDA, Ollama, Kokoro speech out. |

### Results (2026-10-28, on the machine in [BENCHMARKS.md](BENCHMARKS.md))

- `pytest`: **120 passed**. Earlier runs passed 119 three times in a row; the 120th test checks
  the example config.
- `pytest -m "models or ollama"`: **3 passed** in 17 s.
- `ruff check src tests`: clean. `mypy`: clean (41 source files).
- `python -m chiku --check`: **All checks passed.** It found the hardware, 3 input devices, all
  models, and Ollama with the model ready. The TTS → STT round trip heard "Chiku, please open
  the calculator." in 0.16 s on CUDA.

## Phase 3 verification (2026-10-28)

### Automated

- `test_research.py`:
  - Brave request shape: key sent in the header, never in the URL.
  - Parsing and HTML stripping; error mapping for 401, 429, 500, network failure and a missing
    key.
  - Search tool states (ready, no_key, off); sources; the untrusted-content note.
  - Read-page permissions: auto for search results, confirm for other URLs, deny when disabled.
  - Article extraction; safe redirects; a private address on a redirect hop is blocked.
  - Rejections: non-text content types, redirect loops, size and text caps.
  - The real host check blocks localhost, 127.0.0.1, 10.x, 192.168.x, 169.254.169.254 and ::1.
- `test_files.py`:
  - Resolution: aliases, bare names.
  - Rejection: `..`, absolute paths outside the roots, UNC paths, ADS, `CON`/`NUL`, disabled
    folders, and a **real NTFS junction escape**.
  - Find, list, open (programs need confirmation), read (text only).
  - Create, move/rename (confirm, no overwrite, no moving roots or a folder into itself),
    recycle (confirm, roots protected).
  - `-m system`: a **real Recycle Bin delete** through `SHFileOperationW`, on a temp file.
- `test_windows_ctl.py`: window matching (catalog exe, process, title), listing, switching
  (including focus blocked), closing (confirm, verify, "may be asking to save"), media keys, and
  real `EnumWindows` listing.
- `test_intents.py`: media, switch and close phrases. Negatives: "stop", "next", "go to
  sleep", "switch to a different topic". Research-query detection, including the local-topic
  exclusions.
- `test_assistant.py`:
  - The research router runs search, reads the top page, attaches sources and says "let me look
    that up".
  - A model-initiated search collects sources.
  - A link in your message is read before answering.
  - Without a key, the prompt explains how to add one; with search disabled, the web tools are
    hidden.
  - Tool output is trimmed in history.
  - Fast paths: media (instant and silent), switch (only if the window exists), close (asks
    first).

Results:

- `pytest`: **216 passed**, two consecutive runs.
- `pytest -m system`: 1 passed.
- ruff and mypy: clean.

### With the real model and real network

| Check | Result |
|---|---|
| `fetch_page("https://ollama.com/blog/thinking")` | 0.75 s, 4,920 characters of article text, correct title |
| Fetching `127.0.0.1:11434`, `localhost`, `169.254.169.254` | All refused with "private or local network address" |
| Wikipedia (`en.wikipedia.org`, REST and Action APIs) | HTTP 403 "robot policy" for this client. Chiku now reports that the site doesn't allow automated reading and uses the snippet. **Not worked around.** |
| GUI: "what does https://ollama.com/blog/thinking say?" | The page was read without a prompt (your link). The answer matched the page. "1. Thinking · Ollama Blog · ollama.com" was shown as a source; meta read "with web sources". |
| Real LLM tool choice (Qwen3-4B, file tools sandboxed in a temp folder) | "find my pyproject file" → `find_files`. "what apps do I have open" → `list_open_windows`. "make a folder…" → `create_folder`. "delete notes.txt" → `find_files`, then she asked aloud before recycling. |
| Weather question with search results whose pages can't be read | She answered from the snippets ("heavy rain… orange alert from IMD"). |
| `--check` | Brave: "no API key". The six known folders resolved, including OneDrive redirection. |
| `--find-files pyproject`, `--windows` | Real results from this PC. |

**Not verified:**

- A real Brave Search query, because no API key has been provided yet.
- Real `switch_to_app` / `close_app` / media keys against your running apps. They would steal
  focus, close windows or change playback; they are covered by tests with a fake backend.

**Incident during testing:** the first real-model run used the real Documents folder. It created
an empty folder `Documents\Chiku Test`, which I then removed (it was empty). Later runs used a
temporary sandbox.

## Phase 5, branding and installer verification (v0.5.0, 2026-10-28)

### Automated

- `test_branding.py`:
  - The original mascot files match their pinned SHA-256 (unmodified).
  - The transparent/background mapping matches the real alpha channels.
  - The `.ico` has 16–256 px sizes and keeps transparency; the avatar is RGBA.
  - The `pyproject` version and licence match the package.
- `test_gpu_pack.py`:
  - Only `nvidia/*/bin/*.dll` files are extracted; a path-traversal member is ignored.
  - A checksum mismatch installs nothing.
  - Hardware recommendations cover NVIDIA PCs (with and without the pack), CPU-only 16 GB and
    8 GB PCs, and 4 GB PCs.
  - The WER function.
- `test_assistant.py`: the clock rides on the newest user message only, and stored history stays
  raw.

Results:

- `pytest`: **292 passed**.
- ruff and mypy: clean (60 source files).

### Real checks on the developer PC

| Check | Result |
|---|---|
| Mascot discovery | Two images found in the project root (the user had renamed them during the session; discovery uses content, not names). The PNG is RGBA with 58.7% fully transparent pixels and transparent corners, so it is the transparent version. The JPG is RGB with uniform #CBCBCB corners, so it is the background version. Moved to `assets/branding/` with byte-identical SHA-256. |
| Derived assets | The `.ico` holds 10 sizes, 16–256 px, with transparency; the build script asserts the originals are unchanged. |
| Branded UI (screenshots) | Header avatar, animated mascot (state and level driven), welcome card, onboarding wizard with watermark art. The wizard detected the i7-13700HX, the RTX 5050 and the Intel UHD, and recommended small.en on GPU plus qwen3:4b-instruct. |
| Benchmark | See [BENCHMARKS.md](BENCHMARKS.md): baseline vs after, the A/B prompt-cache test, and rejected options. |
| PyInstaller build | 416 MB onedir. The `--version-info` smoke test **caught a real packaging bug** (missing `language_tags` data, which would have crashed the voice in the installed app). Fixed; all 16 component checks pass. |
| Frozen app `--check` with real models | All checks passed. The voice round trip transcribed "Chikku, please open the calculator." CUDA was correctly unavailable without the NVIDIA pack and fell back to CPU (1.58 s). |
| Installer build (`packaging\build.ps1`) | `Chiku-Setup-0.5.0.exe`, 124 MB, SHA-256 `7728ad3f…` (full hash in the `.sha256` file). The build moved to `%LOCALAPPDATA%\Chiku-build` after OneDrive/antivirus locked output inside the synced project folder. |
| Installer welcome page (screenshot) | Chiku icon; GPL licence page. The welcome page with the mascot art was enabled afterwards (`DisableWelcomePage=no`); **its rendering was not re-captured**. |
| `packaging\verify_install.ps1` (0.5.0 → 0.5.1) | **14/14 PASS:** silent per-user install (51 s); both exes present; Start menu shortcut; HKCU uninstall entry; no unrequested desktop shortcut; installed CLI loads all components; GUI window "Chiku" appears; upgrade replaces the version; upgraded app runs; upgrade kept user data; uninstall removed files, shortcut and registry entry; uninstall kept user data. Afterwards: no leftover process, shortcut or registry key. |
| NVIDIA pack metadata (real PyPI) | Pinned versions exist: cublas 528 MB, cudnn 709 MB, on `files.pythonhosted.org`, with SHA-256 published. **The full 1.2 GB pack download was not run.** |

**Not verified:**

- The interactive uninstall prompt, and `/DELETEUSERDATA=yes`. Running them on the developer PC
  would delete real data.
- "Install for all users" (admin).
- Upgrading while Chiku is running (the AppMutex path).
- The installer on other PCs or GPUs.
- The full NVIDIA pack download.
- The CI and release workflows: GitHub Actions has not run yet, because nothing is pushed.
- Code signing: the installer isn't signed, so SmartScreen may warn.

## Phase 4 verification (2026-10-28)

### Automated

- `test_memory.py`:
  - Add, update, delete, search, export and clear; case-insensitive dedupe; persistence across
    instances.
  - Eight kinds of secret are refused; ordinary facts that contain numbers are allowed.
  - Prompt-section limits.
  - Remember permissions: asked → auto, model-initiated → confirm, disabled → deny,
    trusting mode.
  - Multi-fact split.
  - Forget: compound entry → confirm; exact single fact → auto; unknown id; search.
- `test_openai_compat.py`:
  - SSE text and fragmented tool-call assembly; Bearer key; wire format for the tool round trip.
  - 401, 404, 429 and 500 mapping; unreachable server; cancel.
  - The model list is checked in `status`.
  - A cloud URL is refused unless allowed, is labelled CLOUD when allowed, and `make_llm`
    degrades to `UnavailableProvider`.
- `test_manage.py`: list, loaded VRAM and capabilities; pull progress; pull aborted when disk is
  too small; pull error and cancel; delete and 404; name validation (rejects `../x`, `x;rm`,
  `-flag`, …); unreachable Ollama.
- `test_intents.py`:
  - Remember and forget phrasing, including "also", "btw" and compound facts.
  - Negatives: "remember to buy milk", "forget it", "I can't remember".
  - "Suggest a dinner for tonight" no longer triggers web search.
- `test_assistant.py`:
  - The remember fast path saves the note, and the next prompt contains it.
  - A model-initiated save asks first; declining saves nothing.
  - The forget fast path works.
  - Memory and memory tools are withheld from a cloud provider unless allowed.
  - Disabled tools are hidden and refused, even via a fast path.

Results:

- `pytest`: **283 passed** (two consecutive runs).
- ruff and mypy: clean (54 source files).

A test caught a real **deadlock** in `MemoryStore.add` on duplicate facts (a non-reentrant lock
re-acquired via `get()`); it was fixed with an `RLock`. The suite now runs with
`-o faulthandler_timeout=30` so any future hang dumps a stack trace.

### With the real model (temporary memory store; your real `memory.db` untouched)

| Turn | Result |
|---|---|
| "Remember that I'm vegetarian and I live in Pune." | Fast path; stored as **two** notes |
| "Also please remember my sister's name is Asha." | Fast path; stored. Before the fix, "Also" bypassed the fast path and the model *claimed* to remember without saving anything. |
| "Suggest a dinner for tonight in one sentence." | Vegetarian paneer suggestion (memory used); no web search (a false trigger was removed) |
| "What's my sister called?" | "Your sister's name is Asha." |
| "Forget that I live in Pune." | Only the Pune note was deleted; "User is vegetarian" kept. Before the fix, the combined note was deleted entirely. |

`--models` lists both installed models with capabilities, and shows 3.9 GB VRAM loaded for the
active one. The Memory, Activity, Models and Settings → Tools windows were rendered against real
data and checked.

**Not verified:**

- Downloading or deleting a model from the UI, against the real Ollama (not run, to avoid
  changing your models).
- A real LM Studio or llama.cpp server (none is installed).
- Any real cloud provider (none chosen).

## Manual verification

### Done by the developer on this machine

| Check | How | Result |
|---|---|---|
| App launches as a Windows desktop window | Scripted run of `MainWindow` with screenshots | Window rendered and ready in 5.1 s. Component strip showed VAD, speech-to-text, AI model and voice all ✓. |
| Typed conversation in the GUI | Typed a question in the window | Reply shown with the `local · qwen3:4b-instruct… (Ollama)` label. |
| Microphone detected and capturing | `--mic-test 4`, plus the GUI mic toggle | 125 frames in 4 s, no drops or overflows. GUI: `mic_active=True`, button lit, state went to "Hearing you…" (the room had background sound). Turning it off stopped the stream (`mic_active=False`, state idle). |
| Speech output plays | `--say "…"` | 3.4 s of audio played on "Headphones (Realtek)" with no errors. **Whether it was audible was not confirmed by a human.** |
| Spoken app command works and is verified | `--chat "Chiku, open Notepad."` | The fast path opened Notepad and verification passed ("Notepad is open."). |
| LLM-driven tools | `--chat` "check whether Spotify is installed … and open it" | `find_app`, then `open_app`, and Spotify opened with verification passed. |
| System info tool | `--chat` battery question | Called `get_system_info(topic=battery)` and answered "96%, plugged in". |
| Full voice pipeline with real models | Integration test (synthesized speech fed as mic frames) | Transcript correct, reply "4", reply audio synthesized. |
| Interrupt latency | `bench_runtime.py` | Median 11 ms from interrupt to turn stopped. |

### To be checked by a person (needs ears, a voice and a room)

Run `.\.venv\Scripts\python.exe -m chiku`, then check:

1. Press **Mic** and say "Hi Chiku, how are you?". The state goes Listening → Hearing you →
   Thinking → Speaking, and you hear a natural female voice reply.
2. Ask a follow-up that depends on the previous answer, for example "why?", and confirm the
   context carried over.
3. Say "Chiku, open Chrome". She acknowledges, Chrome opens, and she confirms. Say "open
   Photoshop" (or any app you don't have) and she says she couldn't find it.
4. While she is speaking a long answer, start talking. With headphones, she should stop within
   about half a second and listen. Also try Esc and the Stop button.
5. Say "open command prompt". A confirmation dialog appears; choosing No means nothing opens.
6. Unplug the USB microphone while the mic is on. Within a few seconds the status shows a
   microphone problem and the Mic button turns off. Plug it back in and press Mic again.
7. Quit Ollama from the tray and ask something. She says she can't reach her language model,
   and the strip shows `AI model ✕`. Restart Ollama and ask again.
8. In Settings → Voice, try other voices (for example `af_bella`, `bf_emma`) with **Test
   voice**. Change the speaking pace and save.
9. Check `%LOCALAPPDATA%\Chiku\audit\actions.jsonl` lists the actions, and that
   `%LOCALAPPDATA%\Chiku\logs\chiku.log` contains no transcript text.

Phase 3 checks:

10. Add a Brave key (Settings → Web). Ask "what's the weather in <your city> tomorrow?". You
    should hear "Let me look that up", then an answer that names its source, with numbered links
    under the reply. Clicking one opens it in your browser.
11. Ask "search for the latest Ollama release". The transcript shows the search line and
    sources.
12. Say "find my resume" (or any file you have), then "open it". Say "make a folder called Test
    in Documents", then "move it to the Desktop" (a dialog appears), then "delete it" (a dialog
    appears, and the folder is in the Recycle Bin afterwards).
13. With music playing, say "pause the music", "next song", "turn it up". Say "switch to
    Chrome" with Chrome open behind other windows. Say "close Notepad" with unsaved text
    (Notepad should ask to save, and Chiku says it may be asking).

Record any failures together with the relevant lines from `chiku.log`.

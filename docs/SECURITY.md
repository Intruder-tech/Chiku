# Security and privacy

## Privacy guarantees (Phase 1)

| Data | Where it goes |
|---|---|
| Memory | `%LOCALAPPDATA%\Chiku\memory.db` (plain SQLite, protected by your Windows account; not synced). Entries are added only when you ask or approve. Secrets are refused. Memories are included in the prompt for local models only, unless you enable "Include memories when using a cloud provider". You can view, edit, delete, export and wipe everything in the Memory window. |
| Cloud provider (optional, off) | Only when you pick an OpenAI-compatible server that isn't on this PC **and** tick "Allow a cloud provider", after a disclosure dialog. The provider then receives: the transcribed text of your messages, the conversation so far, tool results (web page or file text Chiku read), and the system prompt, without memories unless you allow them. Microphone audio is never sent. Every reply is labelled `CLOUD`. |
| Web searches | Only the query words go to Brave Search (`api.search.brave.com`), with the API key in a request header. Every search appears in the transcript as "Brave Search: “query”". Search can be turned off in Settings → Web. |
| Page reading | Chiku connects directly to the site of a result, or a link you gave her. It sends a plain User-Agent and no cookies. Page reading can be turned off. |
| Files | Read only on request, and only inside your six known folders. File contents are never sent anywhere; they go only to the local model. |
| Microphone audio | Captured only while the Mic button is on, or while the opt-in wake word is on (the tray icon then shows a green dot; paused on the lock screen). Audio is held in memory for the current utterance, then discarded. It is never written to disk and never sent anywhere. Wake-word checks transcribe short segments locally and discard the text; it is never shown, stored or logged. |
| Transcripts and replies | They live in memory for the session's context and are shown in the window. They are not saved to disk unless you tick "Keep conversation history" (Settings → Privacy, off by default); then the chats' text is kept in `%LOCALAPPDATA%\Chiku\conversations.json`, and unticking it deletes that file. They are never logged: log lines record lengths and timings only. |
| Discord presence (optional, off) | When ticked, Chiku tells the local Discord app (IPC pipe, no network calls from Chiku) to show "Chatting with Chiku · Local AI voice assistant", a start time, the mascot and two links to the GitHub repository. Nothing else: no conversation text, app names, memories or files. Discord then shows it to people who can see your profile. Unticking it or quitting Chiku clears it. |
| Profile | Your name, pronunciation, interests and optional profile picture (`%LOCALAPPDATA%\Chiku\avatar.png`) stay on this PC. Profile → Export my data writes everything Chiku keeps to a JSON file you choose; Delete my data removes memories, the action log, saved chats, the picture and old logs. |
| LLM requests | Go to Ollama on `127.0.0.1`. A non-loopback Ollama URL is refused unless the user enables "allow remote". In that case every reply is labelled `REMOTE <host>`. |
| Model downloads | Happen only through `--download-models`, which shows sizes and asks first. Downloads come from GitHub releases and Hugging Face, with SHA-256 pinned for the Kokoro and Silero files. |
| Tool actions | Each one is appended to `%LOCALAPPDATA%\Chiku\audit\actions.jsonl` with timestamp, tool, arguments (truncated), approval type, result and duration. |
| Settings | `%APPDATA%\Chiku\settings.json`, which never contains secrets. |
| Secrets | None in Phase 1. Future keys go to Windows Credential Manager through `chiku.credentials`. |

No cloud provider is implemented. Every reply carries the provider label, so a reply can't
claim to be local when it wasn't.

## Permissions model

| Action | Default |
|---|---|
| Open an installed app | Automatic |
| Open terminals, PowerShell, Registry Editor, `.msc`/MMC and other admin tools | Confirmation dialog |
| Open an https URL on an allow-listed domain | Automatic |
| Open any other URL, http (not https), or a raw IP address | Confirmation dialog |
| `file:`, `javascript:`, `ms-settings:`, URLs with embedded credentials | Rejected |
| Read system info | Automatic |
| Web search | Automatic when enabled and a key is set; shown in the transcript |
| Read a web page from search results or from your own message | Automatic |
| Read any other web page (e.g. one a page suggested) | Confirmation dialog |
| Fetch private, loopback or link-local addresses (including on any redirect hop) | Rejected |
| Find, list, open and read files in the allowed folders | Automatic |
| Open programs, scripts or shortcuts (`.exe`, `.bat`, `.ps1`, `.lnk`, …) | Confirmation dialog |
| Create a folder | Automatic (audited) |
| Move or rename | Confirmation dialog; never overwrites |
| Delete | Confirmation dialog; Recycle Bin only, with Windows' own warning if it can't be recycled |
| Anything outside Desktop/Documents/Downloads/Pictures/Music/Videos | Rejected, including via `..`, junctions and symlinks |
| Switch to an app, media keys, list windows | Automatic |
| Remember something you asked it to remember | Automatic |
| Remember something the model suggested | Confirmation dialog (configurable) |
| Forget a memory you asked to forget, when the entry is just that fact | Automatic |
| Forget an entry holding more than you asked, or one the model chose itself | Confirmation dialog |
| Store passwords, PINs, OTPs, card numbers, API keys, PAN, Aadhaar, SSN | Refused |
| Any tool switched off in Settings → Tools | Refused, and hidden from the model |
| Download or delete a model | Your click in the Models window; a download is refused if it would leave less than 2 GB free |
| Close an app | Confirmation dialog (configurable); uses `WM_CLOSE` so apps can ask to save |
| Delete files, change settings, install software, run elevated commands, payments | Not implemented. No tool exists, so the model cannot do these. |

Confirmation dialogs default to "No" and time out to "deny" after 120 s. The app runs as a
normal user and never requests elevation.

## Installer and downloads (v0.5)

- **No admin rights.** The installer is per-user by default (`PrivilegesRequired=lowest`) and
  installs to `%LOCALAPPDATA%\Programs\Chiku`. "All users" is offered explicitly and triggers
  the normal UAC prompt. It runs no privileged commands and changes no unrelated settings: it
  only creates Start menu and optional desktop shortcuts plus an uninstall entry.
- **Upgrades.** The installer waits for the running app to close (AppMutex), replaces the
  program files (including stale `_internal` files) and never touches user data.
- **Uninstall.** It asks whether to delete personal data; the default is **No**. Silent
  uninstalls keep the data unless `/DELETEUSERDATA=yes` is passed. Ollama, its models and
  Credential Manager entries are never removed by the uninstaller.
- **Models are not bundled.** First-run setup shows the sizes and downloads only what you
  confirm. Kokoro and Silero files are checked against pinned SHA-256 hashes. Whisper comes
  from the official Systran repositories on Hugging Face.
- **NVIDIA speech pack (optional).** It fetches the two official wheels from PyPI over HTTPS
  and accepts only `files.pythonhosted.org` URLs. Each file's SHA-256 is verified against the
  digest PyPI publishes, and on a mismatch nothing is installed. Only `nvidia/*/bin/*.dll`
  files are extracted; path traversal in archive members is rejected. Nothing from the pack is
  executed except loading the DLLs.
- **Release integrity.** Every installer is published with a `.sha256` file. Builds happen in
  GitHub Actions from tagged commits. The installer is **not code-signed yet**, so Windows
  SmartScreen may warn on first run.

## Threat model and mitigations

- **Prompt injection, through tool results or future web content.** The system prompt tells the
  model to treat tool output as untrusted data. More importantly, the model's power is limited
  to the registered tools. Each one validates input (pydantic, `extra="forbid"`) and goes
  through permission checks, so even a fully compromised model can only open catalogued apps
  and allow-listed sites. Anything else needs a human click.
- **Arbitrary command execution.** There is no shell tool. App launches use catalog entries
  from the Start Menu and `Get-StartApps`, never strings from the model. Shortcuts that point at
  scripts (`.bat`, `.cmd`, `.ps1`, `.vbs`, `.js`, `.wsf`) are excluded. AppIDs containing quotes
  or control characters are dropped. `subprocess` is always called with an argument list, never
  `shell=True`.
- **Discovery script.** A fixed, inline PowerShell script runs with
  `-NoProfile -NonInteractive`. No user or model input is interpolated into it.
- **Prompt injection through web pages and files (Phase 3).** Page and file text goes back to
  the model as `untrusted_content`, with a note that it is data, not instructions. The page
  reader only opens URLs that came from a search or from the user without asking. A link that
  injected text tries to get Chiku to visit (for example, to exfiltrate data in a query string)
  needs a human click. Injected text can't reach dangerous actions either: there is no shell,
  and file changes and app closes always need confirmation. The residual risk is that injected
  text could steer a *search query* or the wording of an answer. Sources are shown so you can
  check them.
- **SSRF.** The page fetcher resolves the host on every redirect hop and refuses any
  non-global address (loopback, RFC 1918, link-local such as cloud metadata, multicast). The
  known gap is DNS rebinding between the check and the connection. That's acceptable for a
  single-user desktop app, and documented here.
- **Path traversal.** Every file path is resolved with `Path.resolve()`, which follows `..`,
  symlinks and junctions, before the containment check. A junction escape is covered by a test.
- **Unsafe URLs.** URLs are parsed and normalised: hosts become IDNA punycode, which defeats
  homograph spoofing in the confirmation text. Only `http(s)` is allowed, credentials are
  rejected, and the length is capped at 2048. Domain allow-listing matches exact domains and
  subdomains only, so `google.com.evil.io` doesn't match.
- **Model output rendered in the UI.** All transcript labels use `Qt.PlainText`, so model text
  can't inject HTML or links.
- **Resource limits.** Network timeouts (3 s connect, 90 s read). Tool deadlines. Utterances
  capped at 30 s. A bounded mic queue that drops audio instead of growing. History trimmed by
  turn count and character budget. A single-instance lock.
- **Covert recording.** Recording is impossible while the Mic button is off: there is no
  stream. The waveform draws real input levels only while a capture stream is active.
- **Supply chain.** Dependencies come from PyPI. Model files carry pinned checksums where the
  source doesn't change (Kokoro, Silero). Whisper weights come from the `Systran/faster-whisper-*`
  repositories on Hugging Face through `faster_whisper.download_model`.

## Dependency and license review

| Dependency | Version tested | License | Notes |
|---|---|---|---|
| PySide6-Essentials | 6.12.0 | LGPL-3.0 | Dynamically linked; fine for personal use and LGPL-compliant distribution. |
| faster-whisper / CTranslate2 | 1.2.1 / 4.8.4 | MIT | |
| Whisper `small.en` weights | – | MIT | |
| onnxruntime | 1.31.1 | MIT | |
| kokoro-onnx | 0.4.7 | MIT | |
| Kokoro-82M model + voices | v1.0 | Apache-2.0 | |
| phonemizer-fork | 3.3.1 | GPL-3.0+ | Used by kokoro-onnx for grapheme-to-phoneme conversion. |
| espeak-ng (bundled by espeakng-loader) | 0.2.4 | GPL-3.0 | Shared library loaded at runtime. **Any binary redistribution of Chiku must comply with the GPL**, or replace the phonemizer. |
| Silero VAD v5 | 5.1.2 | MIT | |
| sounddevice / PortAudio | 0.5.6 | MIT | |
| httpx | 0.28.1 | BSD-3 | |
| pydantic | 2.14.1 | MIT | |
| keyring | 25.7.0 | MIT | |
| psutil | 7.2.2 | BSD-3 | |
| numpy | 2.5.4 | BSD-3 | |
| nvidia-cublas-cu12 / nvidia-cudnn-cu12 (optional) | 12.9 / 9.27 | NVIDIA proprietary | Optional `[cuda]` extra. |
| Ollama | 0.40.11 | MIT | Separate install. |
| Qwen3-4B-Instruct-2507 | q4_K_M | Apache-2.0 | |

No code was copied from OpenJarvis (Apache-2.0); see [OPENJARVIS.md](OPENJARVIS.md).

## Reporting

There is no external reporting channel yet. Treat the audit log and `chiku.log` as the record of
what Chiku did.

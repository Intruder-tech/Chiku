# ADR-001: Desktop stack — keep PySide6 + PyInstaller + Inno Setup (not Tauri 2)

- Status: accepted (2026-10-28)
- Context: The repository-bootstrap brief asked to "evaluate Tauri 2 and compatible packaging
  options after inspecting the repository".

## Context

Chiku already exists as a working Python application with these parts:

- voice pipeline: Silero VAD, faster-whisper/CTranslate2, Kokoro on onnxruntime, sounddevice
- an Ollama client and a tool system
- a PySide6 (Qt 6) desktop UI
- more than 280 tests

All the heavy work (audio callbacks, ONNX and CTranslate2 inference, CUDA) happens in-process
in Python.

## Options considered

1. **Tauri 2.** A Rust shell and webview UI, with the Python engine as a sidecar process.
   - Pros: small shell binary, web technologies for the UI, a built-in updater and an NSIS/MSI
     bundler.
   - Cons:
     - The whole UI would need rewriting in HTML/JS.
     - It adds a second process and an IPC protocol for every event: state, levels at 30 Hz,
       confirmations.
     - Rust and Node toolchains become build requirements (neither is installed for this
       project).
     - The Python sidecar still has to be frozen with PyInstaller anyway, so it saves no space:
       the shell is small, but the engine dominates the size.
     - WebView2 becomes a runtime dependency.
2. **Keep PySide6, freeze with PyInstaller, and install with Inno Setup.**
   - Pros:
     - zero rewrite
     - one process, so confirmation dialogs and audio levels stay simple and fast
     - an Inno Setup installer can be per-user, branded, handle upgrades (AppMutex, clean
       `_internal`) and ask about user data on uninstall
     - it is already installed and verified here
   - Cons: a larger download than a hypothetical native app. Updates are delivered as a new
     installer rather than an in-app updater.
3. **Briefcase / MSIX.** MSIX sandboxing complicates microphone access, launching other apps,
   and writing to the user's folders, all of which are core features. Briefcase adds little over
   option 2.

## Decision

Option 2. Revisit Tauri only if the UI needs web technology or a much smaller download
becomes critical. Even then, the Python engine would stay as a sidecar.

## Consequences

- `packaging/chiku.spec` builds `Chiku.exe` (windowed) and `chiku-cli.exe` (diagnostics) into one
  folder. Unused Qt modules are excluded.
- `packaging/chiku.iss` builds `Chiku-Setup-<version>.exe`. Models are never bundled; the
  first-run wizard downloads them with the user's consent.
- CI builds the installer on `windows-latest` from a version tag.

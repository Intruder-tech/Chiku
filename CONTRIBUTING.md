# Contributing to Chiku

Thanks for helping! Chiku is a local-first, privacy-respecting voice assistant for Windows.
Contributions are accepted under **GPL-3.0-or-later**. The mascot artwork is not part of the
GPL and may not be modified; see `assets/branding/LICENSE.md`.

## Development setup (Windows 10/11)

```powershell
git clone https://github.com/Intruder-tech/Chiku.git
cd Chiku
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m chiku --download-models   # asks before downloading
.\.venv\Scripts\python.exe -m chiku                     # run the app
```

You'll also want [Ollama](https://ollama.com/download) and one model, e.g.
`ollama pull qwen3:4b-instruct-2507-q4_K_M`. On an NVIDIA GPU, add `pip install -e ".[cuda]"`
for fast speech recognition.

## Before you open a pull request

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -m pytest -o faulthandler_timeout=60
```

CI runs the same checks on `windows-latest`. Tests that need real models, Ollama or audio
hardware are opt-in markers (`-m models`, `-m ollama`, `-m audio`, `-m system`). Mention in the
PR whether you ran them.

## Ground rules

- **Safety:** every action goes through a `Tool` (typed pydantic input, a permission level,
  a timeout) and the `ToolExecutor`. Never add a tool that runs model-generated shell commands.
  Anything destructive, privileged or outward-facing needs `Permission.CONFIRM`.
- **Privacy:** don't log transcripts or replies. Don't send anything off the machine unless the
  user has turned it on, and label it in the UI when it happens.
- **Honesty:** don't claim hardware or features were tested when they weren't. Update
  `docs/TESTING.md` and `docs/BENCHMARKS.md` with real numbers, and `python -m chiku --benchmark`
  prints them.
- **Style:** ruff and mypy must be clean. Lines are at most 100 characters. Use type hints
  everywhere.
- Keep pull requests focused, and add tests for behaviour you change.

## Building the installer

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[packaging]"
winget install JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Output: `packaging\output\Chiku-Setup-<version>.exe`, with a `.sha256` file alongside.

## Releases

1. Update the version in `pyproject.toml` and `src/chiku/__init__.py`.
2. Add an entry to `CHANGELOG.md`.
3. Push a tag such as `v0.5.0`. The release workflow then builds the installer and creates a
   GitHub Release with the installer and its checksum.

## Reporting problems

Open an issue that includes:

- the output of `chiku-cli.exe --check` (or `python -m chiku --check`)
- the relevant lines from `%LOCALAPPDATA%\Chiku\logs\chiku.log`, which contain no transcripts

Report security issues privately; see `docs/SECURITY.md`.

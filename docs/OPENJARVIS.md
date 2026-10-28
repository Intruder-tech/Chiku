# OpenJarvis evaluation

I reviewed the reference repository at <https://github.com/open-jarvis/openjarvis> on
2026-10-28, using its README and repository overview.

## What it is

OpenJarvis is a local-first framework for personal AI agents, licensed under Apache-2.0. Its
main features:

- Written in Python 3.10+, with a Rust extension and a Tauri desktop GUI that needs Node 22+.
- Uses Ollama as the default inference engine and installs a starter model; the full docs
  mention cloud engines.
- Ships eight built-in agents: ReAct, CodeAct (generates and runs Python), monitoring, and
  others. They run on demand, on a schedule, or continuously.
- Skills follow the agentskills.io format.
- Presets include code execution, file I/O and shell access, plus OAuth connectors such as
  Gmail and Calendar.
- Tracks energy, FLOPs, latency and cost, and runs a learning loop over local traces.
- Supports native Windows installs (PowerShell) and WSL2.
- Voice: text-to-speech is used for a spoken morning digest. **There is no live
  speech-to-text, voice activity detection, or barge-in conversation pipeline.**

## Decision

Chiku does **not** fork or vendor OpenJarvis, and no code was copied. Reasons:

1. **The core requirement is missing.** Live two-way voice with VAD, barge-in and streaming TTS
   is the core of Chiku, and OpenJarvis doesn't provide it. Chiku would have to build it either
   way.
2. **The security posture differs.** Its presets centre on CodeAct, shell and file access. Chiku
   deliberately exposes only a small set of typed, permission-checked tools, and has no shell or
   code execution.
3. **Weight.** A Rust extension, a Tauri plus Node GUI, schedulers and a learning loop add a lot
   of install surface that a single-user voice assistant doesn't need.

## Ideas adopted, as independent implementations

- **Local first, cloud optional and explicit.** Ollama is the default engine, behind a
  provider interface (`chiku.llm.base.LLMProvider`). Every reply carries a provenance label, and
  remote endpoints need an explicit opt-in.
- **Measure what matters.** Chiku records per-turn latency metrics (STT time, LLM first token,
  end of speech to first audio). These appear in the log and in the status tooltip, and feed
  [BENCHMARKS.md](BENCHMARKS.md).
- **A tool and skill registry as the extension point.** `ToolRegistry` and `ToolExecutor`
  play that role. Future skills (web search, memory) register as tools with schemas and
  permissions.

## Possible future reuse

If later phases need scheduled agents or a skills marketplace, OpenJarvis's skill format could
be wrapped as Chiku tools, provided each one gets a typed schema and a permission level.
Apache-2.0 allows reuse with attribution and a NOTICE file.

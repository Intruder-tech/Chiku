# Benchmarks

## v0.5.3 (2026-10-28)

`python -m chiku --benchmark 3` on the same laptop (RTX 5050 Laptop GPU, Whisper `small.en` on
CUDA, `qwen3:4b-instruct-2507-q4_K_M`). **Conditions:** 2.3 GB RAM free, 6.1 GB in the page
file, CPU 18% busy before the run, on mains power. Full report:
`%LOCALAPPDATA%\Chiku\logs\benchmark-20261028-181224.md`.

| Metric | v0.5.3 | Notes |
|---|---|---|
| STT latency, small.en on CUDA (3–4 s utterances) | 0.16–0.18 s | WER 0 / 0 / 0.09 (the "14th" again) |
| Kokoro real-time factor | 0.42–0.66 | Less memory pressure than the v0.5.0 runs |
| LLM first token, warm cache | 0.03–0.05 s | |
| Median end of speech → first audio | 4.67 s (3.04 / 4.67 / 7.64) | Varies with reply length and paging |
| Cancellation | 5–13 ms | |
| Process peak RSS | 1372 MB | |

New in v0.5.3, measured separately:

| Metric | Result | Method |
|---|---|---|
| Multilingual Whisper `small`, Hindi/English/Spanish/French detection and transcription | 0.19–0.45 s; Hindi detected with p 0.89–0.99 | Kokoro voices speaking each language |
| Wake word recognition (`tiny.en` + "Hey Chiku. Wake up." prompt) | 17/18 phrases; 0/16 ordinary sentences and 0/10 noise clips falsely woke | Five Kokoro English voices plus Hindi voices; synthetic speech, not real rooms |
| Wake word check time | ~0.38 s per speech segment (2 CPU threads) | Only segments found by VAD are checked |
| CPU while waiting for the wake word | ~5% of one core in silence; ~35% of one core with non-stop speech nearby | `time.process_time` over 22 s of silence and 30 s of continuous speech |
| `winget list` (220 apps) for the Apps page | 1.2 s (previously timed out after 120 s) | Pipe fix, see the changelog |

## Phase 5 (v0.5.0, 2026-10-28): reproducible benchmark and optimisations

Run `python -m chiku --benchmark [RUNS]`. It is silent and local only. It writes
`%LOCALAPPDATA%\Chiku\logs\benchmark-*.{json,md}`, including the hardware, configuration and
**test conditions** (free RAM, page file, power), so you can compare results across PCs.

Synthetic speech from three Kokoro voices (two American, one British; female and male) stands
in for the microphone. The word error rates below are therefore for clean synthetic speech, not
real voices.

**Conditions during these runs:** the developer laptop was heavily loaded, with only
**2.5 GB of 15.7 GB RAM free and 12.8–13.6 GB in the page file**. Chrome, Discord, VS Code and
OneDrive sync were running. Both runs saw the same conditions.

| Metric | Baseline | After Phase 5 | Notes |
|---|---|---|---|
| LLM first token inside a turn, warm cache | 1.74 / 1.89 s | **0.28 / 0.24 s** | Whole-turn effect of the prompt fix below |
| LLM first token after the clock changes (controlled A/B, 4 runs) | 2.07 s median | **0.16 s median** | Time moved from the system prompt into the user message, so Ollama reuses the cached system prompt and tool definitions (~3k tokens) |
| Engine ready, models already on disk | sequential | 2.66 s (parallel loading) | Voice, speech recognition and the LLM load in parallel |
| STT latency, Whisper small.en on CUDA | 0.46–0.50 s | 0.44–0.60 s | Unchanged (GPU) |
| STT word error rate on synthetic speech | 0 / 0 / 0.09 | 0 / 0 / 0.09 | The only "error" is "14th" for "fourteenth" |
| Kokoro voice, real-time factor under memory pressure | 1.74–2.26 | 1.54–2.00 | CPU model weights get paged out |
| Median end of speech → first audio | 6.84 s | 6.19 s | Dominated by the TTS paging above |
| Cancellation | 2–21 ms | 4–23 ms | |
| Chiku process peak RSS | 1326 MB | 1177 MB | |

The same PC with ~4.5 GB RAM free gives a Kokoro real-time factor of **0.28** (fp32, 6
threads). The Phase 1 measurement on an unloaded PC was 1.63 s from end of speech to first
audio.

### Options measured and rejected

| Option | Result | Decision |
|---|---|---|
| Kokoro int8 model (88 MB) | Real-time factor 1.34–1.74, **4–5× slower** than fp32 on this CPU; RSS −120 MB | Rejected (file deleted) |
| Kokoro thread count | 4 threads: 0.32; **6 threads: 0.28**; 8 threads: 0.33 | Keep 6 |
| Kokoro on the GPU via DirectML (onnxruntime-directml 1.24.4, tested in a throwaway venv) | Fails on the model's `ConvTranspose` node ("The parameter is incorrect") | Not possible with this model export |

### Recommendations

- **Free RAM matters most for voice speed.** On machines with less than about 4 GB free, close
  heavy apps. The benchmark report records free RAM, so you can see whether it was the cause.
- An NVIDIA GPU makes speech recognition about 15× faster than an 8-thread CPU (0.3–0.5 s vs
  6–8 s for small.en). Without one, the setup picks base.en or tiny.en.

---

## Earlier measurements (Phase 1)

These numbers were measured on 2026-10-28 on the development machine. None of them are
estimates. Each section gives the method so you can reproduce it.

## Hardware and software

- Intel Core i7-13700HX: 16 cores / 24 threads.
- 15.7 GB RAM. About 5 GB was free during the tests, because other apps were running.
- NVIDIA GeForce RTX 5050 Laptop GPU: 8151 MiB, driver 617.42.
- Windows 11 Home 10.0.26300.
- Python 3.14.0 in a venv.
- Ollama 0.40.11 running `qwen3:4b-instruct-2507-q4_K_M` with num_ctx 8192. Ollama reports
  "100% GPU" and 3.9 GB for it.
- faster-whisper 1.2.1 / CTranslate2 4.8.4 with `small.en`.
- Kokoro-82M through kokoro-onnx 0.4.7 on CPU (onnxruntime 1.31.1, 6 intra-op threads).

## Speech recognition (Whisper `small.en`)

**Method.** Kokoro synthesized a 4.63 s sentence, which was resampled to 16 kHz. I then timed
`WhisperSTT.transcribe` over repeated runs. Scripts: `bench_stt.py` and `bench_cuda.py`, run
during development.

| Device | Runs (s) | Transcript correct |
|---|---|---|
| CPU int8, 4 threads | 8.26, 8.88, 12.57, 8.11 | yes |
| CPU int8, 8 threads | 8.87, 7.88, 6.54, 6.34 | yes |
| CPU int8, 16 threads | 4.04, 2.21, 2.25, 2.26 | yes |
| **CUDA float16** | **0.40, 0.27, 0.27, 0.41, 0.49** | yes |

The first CUDA model load took 53.6 s. The likely cause is a one-time kernel compile for the
Blackwell GPU, but I didn't confirm that. Later loads took 6.6 s.

## Text-to-speech (Kokoro, `af_heart`, CPU)

| Item | Result |
|---|---|
| Model load | 2.2 s |
| First synthesis (warm-up) | 4.7 s for 1.2 s of audio. The app does this during startup. |
| Warm synthesis | 1.24 s for 3.58 s of audio (real-time factor 0.35) |
| Warm synthesis | 0.78 s for 2.07 s of audio (real-time factor 0.38) |

## Full voice turn, end to end

**Method.** `tests/test_integration.py::test_full_voice_turn_with_real_models` feeds a spoken
question ("Hey Chiku, what is two plus two?") into the real engine, frame by frame at real-time
pace. It runs through Silero VAD, Whisper on CUDA, Ollama, and Kokoro. Only the microphone and
the speaker are replaced.

| Metric | Value |
|---|---|
| Utterance length | 2.4 s |
| STT time | 0.165 s |
| LLM time to first token | 0.523 s |
| Reply start to first audio ready | 1.462 s |
| **End of speech detected → first audio** | **1.631 s** |

End of speech is detected after a 700 ms pause (`audio.min_silence_ms`). So for the user, the
time from stopping talking to hearing Chiku is about 2.3 s. That breaks down as the 0.7 s pause,
0.17 s STT, 0.5 s LLM first token, and about 0.9 s of Kokoro work on the first sentence.

The CLI `--chat` text turn measured 0.83 s from reply start to audio for the built-in "open
Notepad" command. That path doesn't involve the LLM.

## Startup, memory, cancellation

**Method.** `bench_runtime.py` builds the real assistant with a muted player, waits for ready,
then starts eight long LLM replies and interrupts each one 0.3 s after the first token.

| Metric | Value |
|---|---|
| Python imports | 0.94 s |
| Engine ready (models cached, Ollama model resident) | 3.95 s |
| GUI window ready (scripted run) | 5.1 s |
| Chiku process RSS (Whisper on GPU, Kokoro, Silero, Qt not loaded) | 1123 MiB |
| GPU memory, whole system, with the LLM and Whisper loaded | 5070 / 8151 MiB |
| Cancel latency (`interrupt()` → turn fully stopped), 8 runs | 9, 14, 9, 7, 14, 13, 2, 18 ms; median 11 ms |
| Playback stop on interrupt | Within one 960-sample block at 24 kHz (40 ms). This follows from the design and was not measured separately. |

Before one fix, cancel latency was 5–1326 ms, because the turn waited for an in-flight sentence
synthesis. The cancelled path no longer waits.

## Not yet measured

- Speech recognition accuracy (word error rate) on real human speech, accents, and noisy rooms.
- Time to first audio with a real person talking into the physical microphone. The
  integration test uses synthesized speech.
- CPU and GPU utilisation over a long session.
- Cold start when Ollama has to load the model: this adds the model load time, roughly
  2–4 s on this GPU.

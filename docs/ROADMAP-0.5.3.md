# Chiku v0.5.3 — design and plan

Decisions from the project owner, 2026-10-28.

| Area | Decision |
|---|---|
| Languages | **English and Hindi** as first-class languages, plus **auto-detection of any language** |
| Wake word | **"Wake up"** and **"Hey Chiku"** |
| UI direction | **Pro workspace** |
| Discord | The owner will create the Discord application and provide its ID |

## 1. Multilingual conversation

**Status: implemented, not yet released.** Differences from the plan:

- Typed messages also get a reply language: from the script, or from common words for
  Latin-script languages and romanised Hindi.
- The per-message prefix says "Reply in Hindi". A small model followed this better than being
  told the spoken language.
- Voice choice is per sentence and follows the sentence's script.

- **Speech recognition:** add multilingual Whisper `small` (~480 MB, MIT) next to `small.en`.
  - The new setting `stt.language` takes `auto` (default once downloaded), `en` or `hi`.
  - Use the detected language and its probability from faster-whisper's `info.language`.
  - If the probability is below 0.6, fall back to the user's preferred language.
- **Reply language:**
  - The detected language is passed to the model. The time prefix gains `[Language: Hindi]`,
    so the system prompt stays cacheable.
  - The persona instruction becomes "reply in the user's language".
- **Voice:** choose a Kokoro voice by language, configurable per language.
  - Hindi uses `hf_alpha` or `hf_beta`; English keeps the user's voice; es/fr/it/pt/ja/zh use
    their female voices.
  - With no voice for a language, Chiku replies in text and says so once.
- **Mixed language (Hinglish):** accepted. Chiku replies in the dominant language. Known
  limitation: espeak's Hindi phonemes for Latin-script Hindi are imperfect.
- **Fast paths:** keep the English regexes and add Hindi equivalents for "open", "close",
  "remember" and media commands, e.g. "Chrome kholo", "gaana roko".
- **Tests:** language routing, voice selection, and the prompt prefix. A real Hindi
  synthesis-to-transcription round trip runs under the opt-in `models` marker.

## 2. Notch and compact mode

**Status: implemented, not yet released.** Differences from the plan:

- Size is 400×58 (176 expanded) so the mic button and the level meter fit at 125% scaling.
- Ctrl+Shift+Space switches between the full window and the minimised style chosen in
  Settings (`ui.mini_mode`: notch by default, or the card) instead of cycling all three; the
  tray offers both styles and the notch has a Card button.
- Positions are stored as the centre's fraction of the screen width, so they survive
  resolution and scaling changes. The last screen is `ui.notch_screen`.
- Placement and snapping are pure functions tested with a two-monitor layout; the widget was
  also checked on a real laptop + HDMI monitor setup.

- A new `ui/notch.py`: a frameless top-edge pill (~360×44) holding the mascot, live state,
  waveform and mic.
  - It expands on hover or click to show the last reply and quick actions.
  - It can be dragged along the top edge of any monitor and snaps to the nearest screen's
    top-centre.
  - Its position is remembered per screen in `ui.notch_positions`.
- **Modes:** full window, compact card (exists), and notch. Ctrl+Shift+Space cycles them; the
  tray has all three.
- **Multi-monitor:** `QGuiApplication.screens()` and `screenChanged` re-snap the notch when a
  monitor is unplugged.

## 3. Discord Rich Presence (opt-in, off by default)

**Status: implemented, not yet released.** It uses a small self-written IPC client (no new
dependency). The checkbox is in Settings → Privacy; the Application ID is in the settings
file (`discord.application_id`), not in the UI. Tested against a fake Discord pipe and live
against the Discord desktop app (2026-10-28): the handshake with Chiku's Application ID and
`SET_ACTIVITY` succeeded, the presence stayed up for 45 s without errors, and stopping
cleared it.

- Use the local Discord IPC pipe `\\.\pipe\discord-ipc-N` with a small self-written client
  (no new dependency), or `pypresence` (MIT) if that proves simpler.
- **What it shows:** "Chatting with Chiku" with state "Local AI voice assistant", the large
  image `chiku` (the mascot uploaded as an art asset), a "since" timestamp, and the buttons
  **Get Chiku** → `https://github.com/Intruder-tech/Chiku` and **Releases** → `…/releases`.
- **Never sent:** conversation text, app names, or anything from memory.
- **Settings:** "Show Chiku on my Discord profile" (off) and the Application ID (not secret).
  Chiku retries quietly when Discord isn't running.

## 4. Pro workspace UI

**Status: implemented, not yet released** (with item 5). Differences from the plan:

- There is no separate Home page. Chat is the home page; its welcome card and the right
  panel cover the overview.
- Conversations are a list in the left pane, not tabs. A reopened chat gets its text back as
  model context, but not the tool calls made in it.
- "Open <app>" in the palette sends "open <app>" as a message, so it is visible in the chat
  and takes the same permission path as a spoken request.
- The right panel hides itself below 1060 px wide; Ctrl+. toggles it.

- **Three panes:**
  - **Left:** navigation (Home, Chat, Profile, Memory, Activity, Apps, Settings) plus
    conversations.
  - **Centre:** chat or the current page.
  - **Right:** a context panel with sources, tool results, file and app details, and the
    live voice and mascot.
- **Command palette (Ctrl+K):** actions, apps, settings and recent conversations.
- **Conversations:** several tabs or sessions. Stored on disk only if the user turns on "Keep
  conversation history"; this is opt-in to keep the privacy promise.
- **Apps page:** installed apps, updates (winget), and install with search.
- **Implementation:** build on `AssistantViewModel` and the components; new components get
  offscreen tests.

## 5. Profile page

**Status: implemented, not yet released.** Usage stats are counts of successful actions from
the action log (apps opened, searches, pages read, installs, updates, file actions, memories
saved, this week, most-opened apps). Conversation turns are not counted, because nothing
records them unless history is kept.

Name and pronunciation, avatar (optional image), languages and preferred reply language,
voice per language, interests, communication style (length and tone), the memories grouped by
category, local usage stats (turns, apps opened, searches; computed from the action log), and
data controls (export, delete). Everything stays local.

## 6. Background mode and wake word

**Status: implemented, not yet released** (built second, per the order of work). Differences
from the plan:

- The wake model is the English-only `tiny.en` (same 75 MB) because both phrases are English;
  Devanagari spellings ("हे चीकू") are matched too. Whisper is given "Hey Chiku. Wake up." as
  its prompt, which raised recognition on synthesized speech from 14/18 to 17/18 with no extra
  false wakes.
- Only the first 2.5 s of each segment is transcribed, and segments are cut at 8 s.
- A request in the same breath ("Hey Chiku, what time is it?") is transcribed by the main
  model and answered without a second utterance.
- On waking while hidden, the minimised style (the notch by default) is shown, and it hides
  again when Chiku goes back to waiting.
- The lock-screen pause polls the input desktop every 2 s instead of subscribing to session
  notifications, which keeps it inside the engine with no window handle needed.

- **Autostart (opt-in):** a per-user `HKCU\…\Run` entry runs `Chiku.exe --background`, which
  starts in the tray with no window.
- **Wake detection:** when enabled, the mic runs at low CPU.
  - Silero VAD gates the audio, so only voiced segments of 3 s or less are examined.
  - Whisper `tiny` (multilingual, 75 MB) transcribes those short segments, and Chiku matches
    "wake up" or "hey chiku" (plus fuzzy variants such as "hey chiku" heard as "hey chicku").
  - On a match: play a chime, show the notch, and listen for the request.
  - Nothing is stored or sent.
- **Indicators and safety:**
  - A tray icon shows a "listening for wake word" badge.
  - Pause wake word works with one click and is automatic when the screen is locked.
  - The wake segments are never logged as text.
- **Later:** an openWakeWord custom model, which uses less CPU, may replace the tiny-Whisper
  matcher.

## Order of work

1. Multilingual: STT, voices, prompt, fast paths.
2. Background mode and wake word, plus autostart.
3. Notch, and moving between screens.
4. Pro workspace UI, profile page, and Apps page.
5. Discord presence (once the Application ID is available).
6. Docs, tests, benchmark, installer, and the v0.5.3 release.

## Discord application: steps for the owner

1. Go to <https://discord.com/developers/applications> → **New Application** → name it
   **Chiku**.
2. On **General Information**, copy the **Application ID**.
3. Under **Rich Presence → Art Assets**, upload `assets/branding/discord/chiku-1024.png` with
   the key **`chiku`**. Discord needs at least 512×512, and the original
   `chiku-transparent.png` is only 500×500, so `scripts/build_assets.py` makes 1024×1024
   copies. Optionally upload `chiku-bg-1024.png` as **`chiku_bg`**.
4. Send the Application ID. It is a public identifier, not a secret.

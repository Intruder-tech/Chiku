"""Wake word: "Hey Chiku" or "Wake up" while the microphone is otherwise off.

The listener cuts speech into short segments with voice activity detection; only those
segments are checked, by a small English Whisper model (`tiny.en`, ~75 MB) on the CPU. The
transcripts are compared with the wake phrases and then thrown away: they are never shown,
logged, stored or sent anywhere.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from chiku.config import STTSettings
from chiku.models import whisper_present

WAKE_MODEL = "tiny.en"
RATE = 16000
CHECK_S = 2.5  # only the start of a segment is transcribed: the wake phrase comes first
MAX_SEGMENT_S = 8.0  # longer speech is cut so the next segment gets its own check
_PHRASE_WITHIN = 3  # the phrase must start within the first few words of the segment

# Not "a": "I had a chiku milkshake" is about the fruit.
_GREETINGS = frozenset({"hey", "hi", "hai", "hay", "he", "ok", "okay", "hello", "हे", "है",
                        "हाय", "हेलो"})
_GREETING_PREFIXES = ("hey", "hay", "hi", "he")  # run together: "heychiku", "hechi ku"
# Biases the small model towards spelling the name the way the matcher expects. Measured on
# synthesized speech: wake phrases recognised 14/18 without it, 17/18 with it; no extra
# false wakes on noise or ordinary sentences.
PROMPT = "Hey Chiku. Wake up."
_NAME_DEVANAGARI = frozenset({"चीकू", "चिकू", "चिक्कू", "चीकु", "चिकु"})


@dataclass(frozen=True)
class WakeHit:
    phrase: str  # "hey chiku" or "wake up"
    request: str  # words said after the phrase in the same breath ("" = none)


# \w alone splits Devanagari words at their vowel signs; the danda (U+0964/5) is punctuation.
_WORD = re.compile(r"[\wऀ-ॣ०-ॿ]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFC", text.lower()))


def _sounds_like_chiku(word: str) -> bool:
    """Spellings a speech model produces for "Chiku": chikku, cheeku, chikoo, chico, shiku…"""
    if word in _NAME_DEVANAGARI:
        return True
    w = word.replace("ck", "k").replace("ch", "C").replace("sh", "C").replace("c", "k")
    w = w.replace("q", "k").replace("C", "c")
    for a, b in (("ee", "i"), ("ea", "i"), ("ie", "i"), ("y", "i"), ("oo", "u"), ("ou", "u"),
                 ("o", "u")):
        w = w.replace(a, b)
    w = re.sub(r"(.)\1+", r"\1", w)  # chikku -> ciku
    return w == "ciku"


def _match(words: list[str]) -> tuple[str, int] | None:
    """(phrase, index just past it) when a wake phrase starts within the first words."""
    for i in range(min(_PHRASE_WITHIN, len(words))):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in _GREETINGS and _sounds_like_chiku(nxt):
            return "hey chiku", i + 2
        for used, joined in ((1, w), (2, w + nxt)):
            for g in _GREETING_PREFIXES:
                if joined.startswith(g) and _sounds_like_chiku(joined[len(g):]):
                    return "hey chiku", i + used
        if (w == "wake" and nxt == "up") or (w == "वेक" and nxt == "अप"):
            return "wake up", i + 2
        if w == "wakeup":
            return "wake up", i + 1
    return None


def find_wake(text: str) -> WakeHit | None:
    words = _words(text)
    m = _match(words)
    if m is None:
        return None
    phrase, end = m
    rest = words[end:]
    if rest and _sounds_like_chiku(rest[0]):  # "wake up, Chiku"
        rest = rest[1:]
    return WakeHit(phrase, " ".join(rest))


def strip_wake(text: str) -> str:
    """The request without its leading wake phrase ("Hey Chiku, what time is it?")."""
    words = _words(text)
    m = _match(words)
    if m is None:
        return text.strip()
    end = m[1]
    if end < len(words) and _sounds_like_chiku(words[end]):
        end += 1
    # Cut the original text at the end-th word so the request keeps its punctuation.
    text = unicodedata.normalize("NFC", text)
    spans = list(_WORD.finditer(text))
    if end >= len(spans):
        return ""
    return text[spans[end].start():].strip()


def chime(rate: int = 24000) -> np.ndarray:
    """A short, soft two-note rising chime: Chiku heard her name."""
    parts = []
    for freq, dur in ((660.0, 0.09), (880.0, 0.14)):
        t = np.arange(int(rate * dur)) / rate
        env = np.minimum(1.0, t / 0.01) * np.exp(-t * 18.0)
        parts.append(0.18 * env * np.sin(2 * np.pi * freq * t))
    return np.concatenate(parts).astype(np.float32)


class WakeSpotter:
    """Checks speech segments for the wake phrase with Whisper tiny.en on the CPU."""

    def __init__(self, models_dir: Path, threads: int = 2):
        from chiku.stt.whisper import WhisperSTT

        self.models_dir = models_dir
        # Two threads: background listening must not compete with whatever the user is doing.
        self._stt = WhisperSTT(STTSettings(model=WAKE_MODEL, device="cpu", language="en",
                                           cpu_threads=threads), models_dir, prompt=PROMPT)
        self.name = f"whisper {WAKE_MODEL} (cpu)"

    @property
    def available(self) -> bool:
        return whisper_present(self.models_dir, WAKE_MODEL)

    def load(self) -> None:
        self._stt.load()

    def check(self, audio: np.ndarray) -> WakeHit | None:
        hit = find_wake(self._stt.transcribe(audio[: int(CHECK_S * RATE)]).text)
        if hit is not None and len(audio) > CHECK_S * RATE and not hit.request:
            # Speech went on past the checked part: the main model transcribes the whole
            # segment as a request.
            hit = WakeHit(hit.phrase, "…")
        return hit

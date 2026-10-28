"""Which language the user spoke, which language a reply sentence is in, and which voice says it.

Whisper reports a language code (ISO 639-1, e.g. "hi") with a probability. Chiku replies in
the user's language; each reply sentence is voiced by a Kokoro voice for its script, and
languages without a Kokoro voice are answered in text only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

NAMES: dict[str, str] = {
    "en": "English", "hi": "Hindi", "ur": "Urdu", "bn": "Bengali", "mr": "Marathi",
    "ta": "Tamil", "te": "Telugu", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam",
    "pa": "Punjabi", "ne": "Nepali", "es": "Spanish", "fr": "French", "it": "Italian",
    "pt": "Portuguese", "de": "German", "nl": "Dutch", "ru": "Russian", "uk": "Ukrainian",
    "pl": "Polish", "tr": "Turkish", "ar": "Arabic", "fa": "Persian", "he": "Hebrew",
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "id": "Indonesian", "vi": "Vietnamese",
    "th": "Thai", "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
    "el": "Greek", "cs": "Czech", "ro": "Romanian", "hu": "Hungarian",
}

# Female Kokoro voices per language (English uses the user's chosen voice).
DEFAULT_VOICES: dict[str, str] = {
    "hi": "hf_alpha", "es": "ef_dora", "fr": "ff_siwis", "it": "if_sara", "pt": "pf_dora",
    "ja": "jf_alpha", "zh": "zf_xiaobei",
}
VOICE_PREFIX: dict[str, str] = {lang: v[:2] for lang, v in DEFAULT_VOICES.items()}
_LATIN_VOICED = {"es", "fr", "it", "pt"}

# Whisper often labels spoken Hindi as Urdu (the spoken languages are nearly identical);
# when the user speaks Hindi, Hindi (Devanagari) is the useful transcription.
_CONFUSED_WITH = {"ur": "hi"}
MIN_CONFIDENCE = 0.6

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[一-鿿]")


def name(code: str | None) -> str:
    if not code:
        return "English"
    return NAMES.get(code, code)


def choose(detected: str, probability: float, all_probs: Iterable[tuple[str, float]] | None,
           preferred: list[str]) -> str:
    """The language to transcribe in, given Whisper's detection and the user's languages.

    A confident detection of any language is accepted ("auto-detect any language"). A
    detection below MIN_CONFIDENCE falls back to the most likely of the user's own languages.
    """
    preferred = preferred or ["en"]
    if detected in preferred:
        return detected
    mapped = _CONFUSED_WITH.get(detected)
    if mapped is not None and mapped in preferred:
        return mapped
    if probability >= MIN_CONFIDENCE:
        return detected
    probs = dict(all_probs or ())
    return max(preferred, key=lambda lang: (probs.get(lang, 0.0), -preferred.index(lang)))


def script_language(text: str) -> str | None:
    """Language implied by the writing system, or None for Latin/other scripts."""
    if _DEVANAGARI.search(text):
        return "hi"
    if _KANA.search(text):
        return "ja"
    if _HAN.search(text):
        return "zh"
    return None


def speech_language(text: str, turn_language: str | None) -> str:
    """Language to voice one reply sentence in.

    The script decides first (a Devanagari sentence is Hindi even in an English turn). Latin
    text follows the turn's language only for languages that are written in Latin script or
    have no voice at all; Latin text in a Hindi turn (an app name, an English sentence) is
    spoken by the English voice.
    """
    by_script = script_language(text)
    if by_script:
        return by_script
    if turn_language and turn_language not in ("en", "hi", "ja", "zh") and (
            turn_language in _LATIN_VOICED or turn_language not in DEFAULT_VOICES):
        return turn_language
    return "en"


def voice_for(language: str, english_voice: str, overrides: Mapping[str, str]) -> str | None:
    """Kokoro voice for `language`, or None when Chiku has no voice for it."""
    if language == "en":
        return english_voice
    chosen = overrides.get(language) or DEFAULT_VOICES.get(language)
    if chosen and chosen[:2] != VOICE_PREFIX.get(language, chosen[:2]):
        chosen = DEFAULT_VOICES.get(language)  # e.g. an English voice configured for Hindi
    return chosen


# Very common words that rarely occur in the other listed languages. Enough to tell short
# typed messages apart; Whisper detects the language of speech, so this is for typing only.
_WORD_LISTS: dict[str, str] = {
    "en": ("the a an is are was what how why who where when can could would you "
           "your i me my it this that and of to in for with please tell show hello "
           "hi hey thanks thank okay ok yes no"),
    "hi": ("hai hain kya kaise kaisa kyun kyon mujhe mera meri mere aap aapka tum "
           "hum nahi nahin karo kar karna batao bataiye sunao bolo accha acha theek "
           "thik ek ko ki ka ke se mein main bhi abhi kuch kab kahan yeh ye woh wo "
           "kitna kitne chahiye haan na"),
    "es": ("el la los las es está qué que cómo como por para con una un del al "
           "cuál cuándo dónde hola gracias pero muy mi tu"),
    "fr": ("le la les est sont qu'est-ce que quel quelle comment pourquoi avec pour "
           "une un des du au je tu vous il elle bonjour merci très"),
    "it": ("il lo la gli le è sono che come perché con per una un del della "
           "ciao grazie molto mi ti"),
    "pt": ("os as é está são que como por para com uma um do da não "
           "olá obrigado obrigada muito você"),
    "de": ("der die das ist sind was wie warum wer wo wann und mit für ein eine "
           "nicht ich du sie hallo danke sehr"),
}
_COMMON_WORDS = {lang: frozenset(words.split()) for lang, words in _WORD_LISTS.items()}


def guess_text(text: str) -> str | None:
    """Language of a typed message, or None when it isn't clear."""
    by_script = script_language(text)
    if by_script:
        return by_script
    words = re.findall(r"[a-zà-öø-ÿ'¿¡-]+", text.lower())
    if not words:
        return None
    scores = {lang: sum(w.strip("¿¡") in vocab for w in words)
              for lang, vocab in _COMMON_WORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_second, runner_up) = ranked[0], ranked[1]
    if top == 0 or top == runner_up:
        return None
    return best

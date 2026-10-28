"""Language choice, reply voices, typed-language guessing and Hindi commands."""

import pytest

from chiku.intents import Intent, parse_command
from chiku.language import choose, guess_text, script_language, speech_language, voice_for
from chiku.persona import PersonaSettings, build_system_prompt, time_prefix
from chiku.text import SentenceChunker

# -- which language Whisper should transcribe in ---------------------------------------

def test_preferred_language_is_kept() -> None:
    assert choose("hi", 0.4, None, ["en", "hi"]) == "hi"


def test_urdu_detection_becomes_hindi_for_hindi_speakers() -> None:
    assert choose("ur", 0.9, None, ["en", "hi"]) == "hi"
    assert choose("ur", 0.9, None, ["en"]) == "ur"  # not a Hindi speaker: trust Whisper


def test_confident_other_language_is_accepted() -> None:
    assert choose("es", 0.95, None, ["en", "hi"]) == "es"


def test_unsure_detection_falls_back_to_likeliest_own_language() -> None:
    probs = [("cy", 0.3), ("hi", 0.25), ("en", 0.2)]
    assert choose("cy", 0.3, probs, ["en", "hi"]) == "hi"
    assert choose("cy", 0.3, None, ["en", "hi"]) == "en"  # no scores: first preference


# -- which voice speaks a reply sentence -------------------------------------------------

@pytest.mark.parametrize(("text", "turn", "lang"), [
    ("हो गया।", "en", "hi"),  # the script wins
    ("Google Chrome खोल रही हूँ।", "hi", "hi"),
    ("Done, it's up.", "hi", "en"),  # Latin text in a Hindi turn: English voice
    ("Hola, ¿qué tal?", "es", "es"),
    ("Guten Tag.", "de", "de"),  # no German voice; the caller shows text instead
    ("こんにちは", None, "ja"),
    ("Hello", None, "en"),
])
def test_speech_language(text: str, turn: str | None, lang: str) -> None:
    assert speech_language(text, turn) == lang


def test_voice_for_language() -> None:
    assert voice_for("en", "bf_emma", {}) == "bf_emma"
    assert voice_for("hi", "af_heart", {}) == "hf_alpha"
    assert voice_for("hi", "af_heart", {"hi": "hf_beta"}) == "hf_beta"
    assert voice_for("hi", "af_heart", {"hi": "af_bella"}) == "hf_alpha"  # wrong-language voice
    assert voice_for("de", "af_heart", {}) is None


# -- typed messages ----------------------------------------------------------------------

@pytest.mark.parametrize(("text", "lang"), [
    ("What's the tallest mountain in India?", "en"),
    ("¿Cuál es la capital de Australia?", "es"),
    ("mujhe ek chhota sa joke sunao", "hi"),
    ("मौसम कैसा है", "hi"),
    ("Quel temps fait-il à Paris en avril ?", "fr"),
    ("Wie spät ist es?", "de"),
    ("thanks a lot", "en"),
    ("pizza", None),
])
def test_guess_text(text: str, lang: str | None) -> None:
    assert guess_text(text) == lang


def test_script_language() -> None:
    assert script_language("Chrome") is None
    assert script_language("क्रोम") == "hi"


# -- Hindi commands ------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "intent"), [
    ("Chrome kholo", Intent("open_app", "Chrome", "hi")),
    ("chiku, chrome khol do please", Intent("open_app", "chrome", "hi")),
    ("Chrome खोलो", Intent("open_app", "Chrome", "hi")),
    ("youtube.com kholo", Intent("open_url", "youtube.com", "hi")),
    ("notepad band karo", Intent("close_app", "notepad", "hi")),
    ("गाना रोको", Intent("media", "play_pause", "hi")),
    ("gaana band karo", Intent("media", "play_pause", "hi")),
    ("अगला गाना", Intent("media", "next_track", "hi")),
    ("आवाज़ बढ़ाओ", Intent("media", "volume_up", "hi")),  # nukta as a separate mark
    ("आवाज़ बढ़ाओ", Intent("media", "volume_up", "hi")),  # precomposed nukta letter
    ("आवाज कम करो", Intent("media", "volume_down", "hi")),  # no nukta at all
    ("म्यूट करो", Intent("media", "mute", "hi")),
    ("VLC install karo", Intent("install", "VLC", "hi")),
    ("VLC अनइंस्टॉल करो", Intent("uninstall", "VLC", "hi")),
    ("याद रखना कि मुझे जैज़ पसंद है", Intent("remember", "मुझे जैज़ पसंद है", "hi")),
    ("मेरी बहन का नाम आशा है, ये याद रखना", Intent("remember", "मेरी बहन का नाम आशा है", "hi")),
    ("पिज़्ज़ा वाली बात भूल जाओ", Intent("forget", "पिज़्ज़ा", "hi")),
    ("next song please", Intent("media", "next_track")),  # English stays English
])
def test_hindi_commands(text: str, intent: Intent) -> None:
    assert parse_command(text) == intent


@pytest.mark.parametrize("text", ["मुझे एक कहानी सुनाओ", "क्या आज बारिश होगी", "kya haal hai"])
def test_hindi_conversation_is_not_a_command(text: str) -> None:
    assert parse_command(text) is None


# -- prompt and text shaping ---------------------------------------------------------------

def test_prompt_asks_for_users_language_and_stays_static() -> None:
    p = PersonaSettings()
    a = build_system_prompt(p, model_label="m")
    assert "Devanagari" in a and "language named at the start" in a
    assert a == build_system_prompt(p, model_label="m")  # cacheable: no per-turn content


def test_time_prefix_names_reply_language() -> None:
    assert time_prefix(language="Hindi").endswith(" | Reply in Hindi]\n")
    assert "Reply in" not in time_prefix()


def test_chunker_splits_hindi_sentences() -> None:
    c = SentenceChunker()
    assert c.feed("ठीक है। मैं याद रखूँगी।") == ["ठीक है।", "मैं याद रखूँगी।"]
    assert c.flush() == []


def test_whisper_hallucination_filter_keeps_hindi() -> None:
    from chiku.stt.whisper import _HALLUCINATIONS, _normalise

    assert _normalise("क्रोम खोलो") not in _HALLUCINATIONS
    assert _normalise("धन्यवाद।") in _HALLUCINATIONS
    assert _normalise("Thanks for watching!") in _HALLUCINATIONS

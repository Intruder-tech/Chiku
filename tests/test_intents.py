import pytest

from chiku.intents import Intent, parse_command, research_query


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Chiku, open Chrome.", Intent("open_app", "Chrome")),
        ("open chrome", Intent("open_app", "chrome")),
        ("Hey Chiku, could you please launch Spotify for me?", Intent("open_app", "Spotify")),
        ("Can you open the calculator app", Intent("open_app", "calculator")),
        ("start file explorer please", Intent("open_app", "file explorer")),
        ("Open Visual Studio Code.", Intent("open_app", "Visual Studio Code")),
        ("open youtube.com", Intent("open_url", "youtube.com")),
        ("Open YouTube dot com", Intent("open_url", "youtube.com")),
        ("Pause the music.", Intent("media", "play_pause")),
        ("Chiku, resume", Intent("media", "play_pause")),
        ("play music", Intent("media", "play_pause")),
        ("next song please", Intent("media", "next_track")),
        ("skip", Intent("media", "next_track")),
        ("previous track", Intent("media", "previous_track")),
        ("turn the volume up a bit", Intent("media", "volume_up")),
        ("Louder!", Intent("media", "volume_up")),
        ("turn it down", Intent("media", "volume_down")),
        ("mute", Intent("media", "mute")),
        ("switch to Chrome", Intent("switch_app", "Chrome")),
        ("Chiku, go back to my notepad window", Intent("switch_app", "notepad")),
        ("close Spotify", Intent("close_app", "Spotify")),
        ("please quit the calculator app", Intent("close_app", "calculator")),
        ("open file explorer", Intent("open_app", "file explorer")),
    ],
)
def test_recognised(text: str, intent: Intent) -> None:
    assert parse_command(text) == intent


@pytest.mark.parametrize(
    "text",
    [
        "What's the weather?",
        "open a new tab in chrome",
        "can you open up about your feelings",
        "open chrome and search for cats",
        "I want to open a bakery someday",
        "open the website for my bank",
        "open",
        "stop",  # reserved for interrupting Chiku
        "next",
        "go to sleep",
        "close your eyes",
        "switch to a different topic",
        "play a game with me",
        "what's the next step",
    ],
)
def test_not_a_simple_open(text: str) -> None:
    assert parse_command(text) is None


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Remember that I prefer tea over coffee.",
         Intent("remember", "User prefers tea over coffee")),
        ("chiku, remember my sister's name is Asha",
         Intent("remember", "User's sister's name is Asha")),
        ("please remember I'm vegetarian", Intent("remember", "User is vegetarian")),
        ("don't forget that I have a meeting with Ravi on Fridays",
         Intent("remember", "User has a meeting with Ravi on Fridays")),
        ("Also please remember my sister's name is Asha.",
         Intent("remember", "User's sister's name is Asha")),
        ("btw remember I like jazz", Intent("remember", "User likes jazz")),
        ("Remember that I'm vegetarian and I live in Pune.",
         Intent("remember", "User is vegetarian\nUser lives in Pune")),
        ("forget that I prefer tea", Intent("forget", "I prefer tea")),
        ("Chiku, forget about my sister", Intent("forget", "my sister")),
    ],
)
def test_memory_commands(text: str, intent: Intent) -> None:
    assert parse_command(text) == intent


@pytest.mark.parametrize("text", ["remember to buy milk", "do you remember when we met",
                                  "forget it", "forget everything", "I can't remember"])
def test_not_memory_commands(text: str) -> None:
    got = parse_command(text)
    assert got is None or got.kind not in ("remember", "forget")


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("search for best budget laptops", "best budget laptops"),
        ("Chiku, look up the Kokoro TTS license", "the Kokoro TTS license"),
        ("Is it going to rain in Mumbai tomorrow?", "Is it going to rain in Mumbai tomorrow?"),
        ("Who won the most recent cricket world cup?",
         "Who won the most recent cricket world cup?"),
        ("what's the latest news on the RTX 5050", "what's the latest news on the RTX 5050"),
    ],
)
def test_research_query_detected(text: str, query: str) -> None:
    assert research_query(text) == query


@pytest.mark.parametrize(
    "text",
    ["What apps do I have open right now?", "how much battery do I have today",
     "explain how a GPU works", "tell me a joke", "find my files from today",
     "pause the music", "what is recursion", "Suggest a dinner for tonight in one sentence."],
)
def test_research_query_not_triggered(text: str) -> None:
    assert research_query(text) is None

"""End-to-end behaviour of the conversation engine with fake hardware and models."""

from chiku.assistant import (
    BUILTIN_SOURCE,
    ComponentStatus,
    Message,
    MicChanged,
    State,
    StateChanged,
    ToolActivity,
    TurnMetrics,
)
from chiku.llm.base import LLMUnavailable, StreamChunk, ToolCall
from chiku.tools.registry import ToolExecutor

from .conftest import FakeCapture, Harness, make_registry, wait_for


def final_assistant(h: Harness) -> list[Message]:
    return [m for m in h.of(Message) if m.role == "assistant" and m.final]


def idle_after_reply(h: Harness, n: int = 1) -> bool:
    return wait_for(lambda: len(final_assistant(h)) >= n and h.assistant._current is None)


def test_text_turn_streams_reply_to_speech(harness: Harness) -> None:
    harness.llm.scripts = [[StreamChunk("Hello! "), StreamChunk("Nice to meet you."),
                            StreamChunk("", done=True)]]
    a = harness.started()
    a.submit_text("hi chiku")
    assert idle_after_reply(harness)
    reply = final_assistant(harness)[0]
    assert reply.text == "Hello! Nice to meet you."
    assert reply.source == harness.llm.label  # honest provenance on every reply
    assert harness.tts.spoken == ["Hello!", "Nice to meet you."]
    assert [m.role for m in a._history] == ["user", "assistant"]
    sent = harness.llm.calls[0]
    assert sent[0].role == "system" and "Chiku" in sent[0].content
    # clock rides on the newest user message only; stored history stays raw
    assert sent[-1].role == "user" and sent[-1].content.startswith("[Local time: ")
    assert sent[-1].content.endswith("hi chiku") and a._history[0].content == "hi chiku"
    assert any(isinstance(e, TurnMetrics) for e in harness.events)


def test_open_app_fast_path_skips_llm_and_verifies(harness: Harness) -> None:
    a = harness.started()
    a.submit_text("Chiku, open Chrome.")
    assert idle_after_reply(harness)
    assert harness.llm.calls == []
    assert [e.name for e in harness.recorder.launched] == ["Google Chrome"]
    reply = final_assistant(harness)[0]
    assert reply.source == BUILTIN_SOURCE
    assert "Google Chrome" in reply.text and "Done" in reply.text
    assert "Google Chrome" in harness.tts.spoken[0]  # acknowledgement spoken first
    assert harness.audit.tail()[-1]["tool"] == "open_app"


def test_open_unknown_app_falls_through_to_llm(harness: Harness) -> None:
    a = harness.started()
    a.submit_text("open the pod bay doors")
    assert idle_after_reply(harness)
    assert len(harness.llm.calls) == 1 and harness.recorder.launched == []


def test_failed_launch_is_reported(harness: Harness) -> None:
    harness.recorder.verified = (False, "no new process or window appeared")
    a = harness.started()
    a.submit_text("launch notepad")
    assert idle_after_reply(harness)
    reply = final_assistant(harness)[0]
    assert "didn't work" in reply.text and "couldn't confirm" in reply.text
    tools = harness.of(ToolActivity)
    assert tools[-1].status == "failed"


def test_llm_tool_call_round_trip(harness: Harness) -> None:
    harness.llm.scripts = [
        [StreamChunk(tool_calls=[ToolCall("open_app", {"app_name": "VS Code"})], done=True)],
        [StreamChunk("Visual Studio Code is open now."), StreamChunk("", done=True)],
    ]
    a = harness.started()
    a.submit_text("I want to write some code, can you get my editor up?")
    assert idle_after_reply(harness)
    assert [e.name for e in harness.recorder.launched] == ["Visual Studio Code"]
    second = harness.llm.calls[1]
    tool_msgs = [m for m in second if m.role == "tool"]
    assert tool_msgs and '"ok": true' in tool_msgs[0].content
    assert final_assistant(harness)[0].text == "Visual Studio Code is open now."


def test_sensitive_app_needs_confirmation(harness: Harness) -> None:
    asked = []
    harness.llm.scripts = [
        [StreamChunk(tool_calls=[ToolCall("open_app", {"app_name": "command prompt"})],
                     done=True)],
        [StreamChunk("Okay, I won't.", done=True)],
    ]
    a = harness.started()
    a.confirm_handler = lambda name, desc: asked.append(desc) or False
    a.submit_text("could you get me a command line window")
    assert idle_after_reply(harness)
    assert asked == ["Open Command Prompt"]
    assert harness.recorder.launched == []
    assert '"error": "denied_by_user"' in harness.llm.calls[1][-1].content


def test_invalid_tool_arguments_go_back_to_model(harness: Harness) -> None:
    harness.llm.scripts = [
        [StreamChunk(tool_calls=[ToolCall("open_app", {"name": "chrome", "cmd": "x"})],
                     done=True)],
        [StreamChunk("Sorry, let me try that differently.", done=True)],
    ]
    a = harness.started()
    a.submit_text("start my browser thing")
    assert idle_after_reply(harness)
    assert harness.recorder.launched == []
    assert "invalid_input" in harness.llm.calls[1][-1].content


def test_llm_unavailable_is_spoken_and_reported(harness: Harness) -> None:
    harness.llm.available = False
    harness.llm.scripts = [LLMUnavailable("I can't reach Ollama at http://127.0.0.1:11434.")]
    a = harness.started()
    a.submit_text("what's up")
    assert idle_after_reply(harness)
    assert any("reach my language model" in s for s in harness.tts.spoken)
    bad = [e for e in harness.of(ComponentStatus) if e.component == "language model"]
    assert bad and not bad[-1].ok
    assert any(m.role == "notice" for m in harness.of(Message))


def test_tts_failure_still_shows_text(harness: Harness) -> None:
    harness.tts.fail = True
    harness.llm.scripts = [[StreamChunk("Text still arrives.", done=True)]]
    a = harness.started()
    a.submit_text("hello")
    assert idle_after_reply(harness)
    assert final_assistant(harness)[0].text == "Text still arrives."
    assert any(e.component == "voice" and not e.ok for e in harness.of(ComponentStatus))


def test_interrupt_cancels_generation(harness: Harness) -> None:
    harness.llm.scripts = [[StreamChunk("First sentence. "), StreamChunk("Second sentence."),
                            StreamChunk("", done=True)]]
    harness.llm.block.set()
    a = harness.started()
    a.submit_text("tell me a long story")
    assert wait_for(lambda: a._current is not None)
    a.interrupt("test")
    assert wait_for(lambda: a._current is None)
    assert harness.player.stops >= 1
    assert final_assistant(harness)[0].text.endswith("…")


def test_interrupt_during_playback_marks_history(harness: Harness) -> None:
    harness.llm.scripts = [[StreamChunk("A reply that is being spoken.", done=True)]]
    harness.player.hold.set()
    a = harness.started()
    a.submit_text("talk to me")
    assert wait_for(lambda: harness.player.playing)
    a.interrupt("stop button")
    assert wait_for(lambda: a._current is None)
    assert a._history[-1].content.endswith("[interrupted while speaking]")


def test_voice_turn_from_microphone(harness: Harness) -> None:
    harness.stt.text = "what time is it"
    harness.llm.scripts = [[StreamChunk("It's late.", done=True)]]
    harness.capture = FakeCapture([0.0] * 5 + [0.9] * 15 + [0.0] * 10)
    a = harness.started()
    a.set_mic(True)
    assert idle_after_reply(harness)
    states = [e.state for e in harness.of(StateChanged)]
    assert State.LISTENING in states and State.HEARING in states and State.THINKING in states
    assert harness.stt.calls == 1
    assert [m.text for m in harness.of(Message) if m.role == "user"] == ["what time is it"]
    a.set_mic(False)
    assert wait_for(lambda: a.state is State.IDLE)
    assert not a.mic_active


def test_empty_transcript_does_not_reach_llm(harness: Harness) -> None:
    harness.stt.text = ""
    harness.capture = FakeCapture([0.9] * 15 + [0.0] * 10)
    a = harness.started()
    a.set_mic(True)
    assert wait_for(lambda: harness.stt.calls == 1)
    assert wait_for(lambda: a._current is None and a.state is State.LISTENING)
    assert harness.llm.calls == []


def test_barge_in_stops_playback(harness: Harness) -> None:
    harness.capture = FakeCapture([0.0] * 3 + [0.95] * 10, then_silence=True)
    a = harness.started()
    harness.player.playing = True  # pretend Chiku is talking
    a.set_mic(True)
    assert wait_for(lambda: harness.player.stops >= 1)
    assert any(e.state is State.HEARING for e in harness.of(StateChanged))


def test_no_barge_in_when_disabled(harness: Harness) -> None:
    harness.settings.audio.barge_in = False
    harness.capture = FakeCapture([0.95] * 20)
    a = harness.started()
    harness.player.playing = True
    a.set_mic(True)
    wait_for(lambda: not harness.capture.frames, timeout=2)
    assert harness.player.stops == 0
    assert harness.stt.calls == 0  # Chiku's own voice is not transcribed


def test_microphone_failure_turns_mic_off_with_error(harness: Harness) -> None:
    harness.capture = FakeCapture([], fail_start=10)
    a = harness.started()
    a.set_mic(True)
    assert wait_for(lambda: a.state is State.ERROR, timeout=15)
    assert MicChanged(False) in harness.of(MicChanged)
    assert not a.mic_active


def test_microphone_recovers_after_transient_failure(harness: Harness) -> None:
    harness.capture = FakeCapture([0.0] * 3, fail_start=1)
    a = harness.started()
    a.set_mic(True)
    assert wait_for(lambda: a.mic_active, timeout=5)
    assert harness.capture.starts == 2


def test_history_is_trimmed(harness: Harness) -> None:
    harness.settings.llm.max_history_turns = 2
    a = harness.started()
    for i in range(4):
        a.submit_text(f"message number {i} about something")
        assert idle_after_reply(harness, i + 1)
    last_call = harness.llm.calls[-1]
    users = [m.content.removeprefix(m.content.split("\n")[0] + "\n")
             if m.content.startswith("[Local time:") else m.content
             for m in last_call if m.role == "user"]
    assert users == ["message number 2 about something", "message number 3 about something"]


def test_pronunciations_applied_to_speech_only(harness: Harness) -> None:
    harness.settings.persona.pronunciations = {"Jalaj": "Juh-luj"}
    harness.llm.scripts = [[StreamChunk("Hi Jalaj!", done=True)]]
    a = harness.started()
    a.submit_text("hello")
    assert idle_after_reply(harness)
    assert harness.tts.spoken == ["Hi Juh-luj!"]
    assert final_assistant(harness)[0].text == "Hi Jalaj!"


def test_research_turn_attaches_sources_and_research_prompt(harness: Harness) -> None:
    seen_tools: list[list[str]] = []
    orig = harness.llm.stream_chat

    def spy(messages, tools, cancel):  # noqa: ANN001
        seen_tools.append([t["function"]["name"] for t in tools or []])
        yield from orig(messages, tools, cancel)

    harness.llm.stream_chat = spy  # type: ignore[method-assign]
    harness.llm.scripts = [
        [StreamChunk("According to IMD, heavy rain is likely tomorrow.", done=True)],
    ]
    a = harness.started()
    a.submit_text("Will it rain in Mumbai tomorrow?")
    assert idle_after_reply(harness)
    reply = final_assistant(harness)[0]
    assert [s["url"] for s in reply.sources] == ["https://weather.example/mum",
                                                 "https://imd.example/"]
    # Fresh-information question: searched and top page read before the model answered.
    assert harness.search.queries == ["Will it rain in Mumbai tomorrow?"]
    first_call = harness.llm.calls[0]
    tool_names = [m.tool_name for m in first_call if m.role == "tool"]
    assert tool_names == ["web_search", "read_web_page"]
    assert any("Let me" in s or "One sec" in s for s in harness.tts.spoken)
    system = first_call[0].content
    assert "Research:" in system and "Never invent sources" in system
    assert "web_search" in seen_tools[0] and "read_web_page" in seen_tools[0]
    activity = [e for e in harness.of(ToolActivity) if e.name == "web_search"][-1]
    assert "Stub Search" in activity.message and "Mumbai" in activity.message


def test_model_initiated_search_also_collects_sources(harness: Harness) -> None:
    harness.llm.scripts = [
        [StreamChunk(tool_calls=[ToolCall("web_search", {"query": "kokoro tts"})], done=True)],
        [StreamChunk("It's a small text-to-speech model.", done=True)],
    ]
    a = harness.started()
    a.submit_text("tell me about kokoro text to speech")
    assert idle_after_reply(harness)
    assert harness.search.queries == ["kokoro tts"]
    assert len(final_assistant(harness)[0].sources) == 2


def test_user_link_is_read_before_answering(harness: Harness) -> None:
    harness.llm.scripts = [[StreamChunk("The page says hello.", done=True)]]
    a = harness.started()
    a.submit_text("what does https://blog.example/post say?")
    assert idle_after_reply(harness)
    tools = [m for m in harness.llm.calls[0] if m.role == "tool"]
    assert tools and tools[0].tool_name == "read_web_page"
    assert final_assistant(harness)[0].sources[0]["url"] == "https://blog.example/post"
    assert harness.audit.tail()[-1]["approval"] == "auto"  # user-given link: no prompt


def test_no_search_key_hides_page_reader_and_says_how_to_fix(harness: Harness) -> None:
    harness.search.has_key = False
    a = harness.started()
    a.submit_text("what's new in the news")
    assert idle_after_reply(harness)
    system = harness.llm.calls[0][0].content
    assert "Brave Search API key" in system


def test_search_disabled_hides_web_tools(harness: Harness) -> None:
    harness.settings.web.search_enabled = False
    seen: list[list[str]] = []
    orig = harness.llm.stream_chat

    def spy(messages, tools, cancel):  # noqa: ANN001
        seen.append([t["function"]["name"] for t in tools or []])
        yield from orig(messages, tools, cancel)

    harness.llm.stream_chat = spy  # type: ignore[method-assign]
    a = harness.started()
    a.submit_text("hello")
    assert idle_after_reply(harness)
    assert "web_search" not in seen[0] and "read_web_page" not in seen[0]
    assert "turned off" in harness.llm.calls[0][0].content


def test_long_tool_results_are_trimmed_in_history(harness: Harness) -> None:
    harness.registry = make_registry(harness.settings, harness.catalog, harness.recorder,
                                     search=harness.search, windows=harness.windows,
                                     page_text="x" * 5000)
    harness.executor = ToolExecutor(harness.registry, audit=harness.audit)
    harness.llm.scripts = [
        [StreamChunk(tool_calls=[ToolCall("web_search", {"query": "x y"})], done=True)],
        [StreamChunk(tool_calls=[ToolCall("read_web_page",
                                          {"url": "https://weather.example/mum"})], done=True)],
        [StreamChunk("Summary.", done=True)],
    ]
    a = harness.started()
    a.submit_text("research this")
    assert idle_after_reply(harness)
    third_call_tool = [m for m in harness.llm.calls[2] if m.role == "tool"][-1]
    assert len(third_call_tool.content) > 4000  # full text during the turn
    kept = [m for m in a._history if m.role == "tool"][-1]
    assert len(kept.content) < 1600 and kept.content.endswith("[trimmed]")


def test_media_fast_path_is_instant_and_quiet(harness: Harness) -> None:
    a = harness.started()
    a.submit_text("pause the music")
    assert idle_after_reply(harness)
    assert harness.llm.calls == []
    assert harness.windows.pressed == [0xB3]
    assert harness.tts.spoken == []
    assert final_assistant(harness)[0].source == BUILTIN_SOURCE


def test_switch_fast_path_only_when_window_exists(harness: Harness) -> None:
    a = harness.started()
    a.submit_text("switch to notepad")
    assert idle_after_reply(harness)
    assert harness.windows.focused == [3] and harness.llm.calls == []
    a.submit_text("switch to excel")
    assert idle_after_reply(harness, 2)
    assert len(harness.llm.calls) == 1  # no Excel window: the model handles it


def test_close_fast_path_asks_first(harness: Harness) -> None:
    asked: list[str] = []
    a = harness.started()
    a.confirm_handler = lambda _n, d: asked.append(d) or True
    a.submit_text("close notepad")
    assert idle_after_reply(harness)
    assert asked and "Notepad" in asked[0]
    assert harness.windows.wins == []
    assert "Closed" in final_assistant(harness)[0].text


def test_remember_fast_path_saves_and_prompt_includes_memory(harness: Harness) -> None:
    a = harness.started()
    a.submit_text("Remember that I prefer tea over coffee")
    assert idle_after_reply(harness)
    assert [i.text for i in harness.memory.all()] == ["User prefers tea over coffee"]
    assert harness.llm.calls == []
    assert any("remember" in s.lower() or "noted" in s.lower() or "mind" in s.lower()
               for s in harness.tts.spoken)
    a.submit_text("what should I drink")
    assert idle_after_reply(harness, 2)
    system = harness.llm.calls[0][0].content
    assert "User prefers tea over coffee" in system and "asked you to remember" in system


def test_model_initiated_memory_needs_approval(harness: Harness) -> None:
    asked: list[str] = []
    harness.llm.scripts = [
        [StreamChunk(tool_calls=[ToolCall("remember", {"text": "User likes jazz"})], done=True)],
        [StreamChunk("Okay.", done=True)],
    ]
    a = harness.started()
    a.confirm_handler = lambda _n, d: asked.append(d) or False
    a.submit_text("I've been listening to a lot of jazz lately")
    assert idle_after_reply(harness)
    assert asked == ["Remember: “User likes jazz”"]
    assert harness.memory.all() == []


def test_forget_fast_path(harness: Harness) -> None:
    harness.memory.add("User prefers tea over coffee")
    a = harness.started()
    a.submit_text("forget that I prefer tea")
    assert idle_after_reply(harness)
    assert harness.memory.all() == []
    assert "forgotten" in final_assistant(harness)[0].text


def test_memory_withheld_from_cloud_provider(harness: Harness) -> None:
    harness.memory.add("User lives in Pune")
    harness.llm.is_local = False
    harness.llm.label = "CLOUD · test"
    seen: list[list[str]] = []
    orig = harness.llm.stream_chat

    def spy(messages, tools, cancel):  # noqa: ANN001
        seen.append([t["function"]["name"] for t in tools or []])
        yield from orig(messages, tools, cancel)

    harness.llm.stream_chat = spy  # type: ignore[method-assign]
    a = harness.started()
    a.submit_text("hello there")
    assert idle_after_reply(harness)
    assert "Pune" not in harness.llm.calls[0][0].content
    assert "remember" not in seen[0]
    harness.settings.memory.share_with_cloud = True
    a.submit_text("hello again")
    assert idle_after_reply(harness, 2)
    assert "Pune" in harness.llm.calls[1][0].content


def test_disabled_tool_is_hidden_and_refused(harness: Harness) -> None:
    harness.settings.tools.disabled = ["media_control", "open_app"]
    seen: list[list[str]] = []
    orig = harness.llm.stream_chat

    def spy(messages, tools, cancel):  # noqa: ANN001
        seen.append([t["function"]["name"] for t in tools or []])
        yield from orig(messages, tools, cancel)

    harness.llm.stream_chat = spy  # type: ignore[method-assign]
    harness.llm.scripts = [
        [StreamChunk(tool_calls=[ToolCall("open_app", {"app_name": "chrome"})], done=True)],
        [StreamChunk("I can't.", done=True)],
    ]
    a = harness.started()
    a.submit_text("pause the music")  # fast path skipped: tool is off -> goes to model
    assert idle_after_reply(harness)
    assert "media_control" not in seen[0] and "open_app" not in seen[0]
    assert harness.windows.pressed == []
    assert harness.recorder.launched == []
    assert "tool_disabled" in harness.llm.calls[1][-1].content


def test_levels_are_zero_when_streams_inactive(harness: Harness) -> None:
    a = harness.started()
    harness.player.level = 0.5  # stale value must not show while nothing plays
    assert a.levels() == (0.0, 0.0)


def test_hindi_command_is_acknowledged_in_hindi_with_hindi_voice(harness: Harness) -> None:
    a = harness.started()
    a.submit_text("Chrome kholo")
    assert idle_after_reply(harness)
    assert harness.llm.calls == []
    assert [e.name for e in harness.recorder.launched] == ["Google Chrome"]
    reply = final_assistant(harness)[0]
    assert "खोल" in reply.text and "हो गया" in reply.text
    assert set(harness.tts.voices_used) == {"hf_alpha"}  # Devanagari sentences, Hindi voice


def test_hindi_voice_turn_tells_model_the_reply_language(harness: Harness) -> None:
    harness.stt.text = "आज मौसम कैसा है"
    harness.stt.language = "hi"
    harness.llm.scripts = [[StreamChunk("आज धूप है। "), StreamChunk("Enjoy!", done=True)]]
    harness.capture = FakeCapture([0.0] * 5 + [0.9] * 15 + [0.0] * 10)
    a = harness.started()
    a.set_mic(True)
    assert idle_after_reply(harness)
    a.set_mic(False)
    assert "| Reply in Hindi]" in harness.llm.calls[0][-1].content
    # each sentence gets the voice of its own language
    assert harness.tts.voices_used == ["hf_alpha", None]


def test_typed_language_is_detected_and_sticks(harness: Harness) -> None:
    harness.llm.scripts = [[StreamChunk("Canberra.", done=True)],
                           [StreamChunk("De nada.", done=True)]]
    a = harness.started()
    a.submit_text("¿Cuál es la capital de Australia?")
    assert idle_after_reply(harness)
    a.submit_text("ok")  # too short to tell: stays Spanish
    assert idle_after_reply(harness, 2)
    assert "| Reply in Spanish]" in harness.llm.calls[0][-1].content
    assert "| Reply in Spanish]" in harness.llm.calls[1][-1].content
    assert set(harness.tts.voices_used) == {"ef_dora"}


def test_language_without_voice_is_text_only_with_one_notice(harness: Harness) -> None:
    harness.llm.scripts = [[StreamChunk("Es ist Mittag.", done=True)],
                           [StreamChunk("Gern geschehen.", done=True)]]
    a = harness.started()
    a.submit_text("Wie spät ist es?")
    assert idle_after_reply(harness)
    a.submit_text("Danke sehr, wie nett")
    assert idle_after_reply(harness, 2)
    assert harness.tts.spoken == []  # nothing spoken in a voice that can't pronounce German
    notices = [m.text for m in harness.of(Message) if m.role == "notice"]
    assert len([n for n in notices if "German voice" in n]) == 1

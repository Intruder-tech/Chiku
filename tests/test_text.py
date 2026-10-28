from chiku.text import SentenceChunker, ThinkFilter, clean_for_speech


def feed_all(chunker: SentenceChunker, parts: list[str]) -> list[str]:
    out: list[str] = []
    for p in parts:
        out += chunker.feed(p)
    return out + chunker.flush()


def test_chunker_splits_sentences_as_they_complete() -> None:
    c = SentenceChunker()
    assert c.feed("Hello there") == []
    assert c.feed(". How are") == ["Hello there."]
    assert c.feed(" you? I'm fine") == ["How are you?"]
    assert c.flush() == ["I'm fine"]


def test_chunker_keeps_abbreviations_and_decimals_together() -> None:
    out = feed_all(SentenceChunker(), ["Dr. Smith paid 3.5 dollars, e.g. a lot. ", "Done."])
    assert out == ["Dr. Smith paid 3.5 dollars, e.g. a lot.", "Done."]


def test_chunker_releases_long_first_clause_early() -> None:
    c = SentenceChunker(first_clause_min=40)
    out = c.feed("Well, that is a really interesting question to think about, and ")
    assert out == ["Well, that is a really interesting question to think about,"]


def test_chunker_hard_splits_very_long_text() -> None:
    c = SentenceChunker(max_chunk=50)
    out = c.feed("word " * 30)
    assert out and all(len(x) <= 50 for x in out)


def test_think_filter_removes_blocks_split_across_chunks() -> None:
    f = ThinkFilter()
    text = "".join(f.feed(p) for p in ["Hi <thi", "nk>secret plan</th", "ink> there"]) + f.flush()
    assert text == "Hi  there"


def test_think_filter_drops_orphan_closing_tag() -> None:
    f = ThinkFilter()
    assert (f.feed("</think>\n\nAnswer") + f.flush()).strip() == "Answer"


def test_think_filter_unclosed_block_is_hidden() -> None:
    f = ThinkFilter()
    assert f.feed("<think>still reasoning") + f.flush() == ""


def test_clean_for_speech_strips_markdown_links_and_emoji() -> None:
    raw = "## Title\n- **Bold** item with [a link](https://x.y) 🎉\n1. `code` & https://ex.am/ple"
    assert clean_for_speech(raw) == "Title Bold item with a link code and the link"


def test_clean_for_speech_replaces_code_blocks() -> None:
    assert "put the code in the chat" in clean_for_speech("Here:\n```py\nprint(1)\n```")

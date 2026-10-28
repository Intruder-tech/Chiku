"""Text shaping between the language model and the speech synthesizer."""

from __future__ import annotations

import re

_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "approx",
    "no", "fig", "inc", "ltd", "co",
}

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


class ThinkFilter:
    """Removes <think>...</think> blocks from a token stream (reasoning models)."""

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, delta: str) -> str:
        self._buf += delta
        out: list[str] = []
        while self._buf:
            if self._inside:
                end = self._buf.find(_THINK_CLOSE)
                if end < 0:
                    # keep a tail that could be the start of the closing tag
                    self._buf = self._buf[-(len(_THINK_CLOSE) - 1):]
                    break
                self._buf = self._buf[end + len(_THINK_CLOSE):]
                self._inside = False
            else:
                orphan = self._buf.find(_THINK_CLOSE)
                if orphan >= 0 and (self._buf.find(_THINK_OPEN) < 0
                                    or orphan < self._buf.find(_THINK_OPEN)):
                    # closing tag without an opening one: drop the tag itself
                    out.append(self._buf[:orphan])
                    self._buf = self._buf[orphan + len(_THINK_CLOSE):]
                    continue
                start = self._buf.find(_THINK_OPEN)
                if start < 0:
                    safe = _safe_prefix_len(self._buf, _THINK_OPEN)
                    out.append(self._buf[:safe])
                    self._buf = self._buf[safe:]
                    break
                out.append(self._buf[:start])
                self._buf = self._buf[start + len(_THINK_OPEN):]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self._inside else self._buf
        self._buf = ""
        return rest


def _safe_prefix_len(buf: str, tag: str) -> int:
    """Length of `buf` that cannot be the beginning of `tag`."""
    for k in range(min(len(tag) - 1, len(buf)), 0, -1):
        if tag.startswith(buf[-k:]):
            return len(buf) - k
    return len(buf)


class SentenceChunker:
    """Accumulates streamed text and releases speakable chunks as soon as possible.

    The first chunk may be released at a clause boundary (comma, dash) once it is
    long enough, which cuts time-to-first-audio on long opening sentences.
    """

    def __init__(self, first_clause_min: int = 28, max_chunk: int = 220) -> None:
        self._buf = ""
        self._emitted = 0
        self._first_clause_min = first_clause_min
        self._max_chunk = max_chunk

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            chunk, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if chunk:
                out.append(chunk)
                self._emitted += 1
        return out

    def flush(self) -> list[str]:
        chunk, self._buf = self._buf.strip(), ""
        return [chunk] if chunk else []

    def _find_cut(self) -> int | None:
        buf = self._buf
        for m in re.finditer(r"[.!?…]+[\"')\]]*(?=\s)|\n+", buf):
            end = m.end()
            if m.group().startswith("\n"):
                return end
            if buf[m.start()] == "." and _is_abbreviation_or_number(buf, m.start()):
                continue
            return end
        if self._emitted == 0 and len(buf) >= self._first_clause_min:
            clauses = list(re.finditer(r"[,;:—–](?=\s)", buf))
            if clauses and clauses[-1].end() >= self._first_clause_min * 0.6:
                return clauses[-1].end()
        if len(buf) > self._max_chunk:
            space = buf.rfind(" ", 0, self._max_chunk)
            return space if space > 0 else self._max_chunk
        return None


def _is_abbreviation_or_number(buf: str, dot: int) -> bool:
    before = re.search(r"([A-Za-z.]+)$", buf[:dot])
    if before and before.group(1).lower().rstrip(".") in _ABBREVIATIONS:
        return True
    # single capital letter initial, e.g. "J. R. R."
    if before and len(before.group(1)) == 1 and before.group(1).isupper():
        return True
    # decimal number: digit before and digit after
    return dot > 0 and buf[dot - 1].isdigit() and dot + 1 < len(buf) and buf[dot + 1].isdigit()


_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_URL = re.compile(r"https?://\S+")
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff️‍]+"
)


def clean_for_speech(text: str) -> str:
    """Strip markdown and other things that sound bad when read aloud."""
    text = _CODE_FENCE.sub(" I've put the code in the chat. ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("the link", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = _EMOJI.sub("", text)
    text = text.replace("&", " and ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

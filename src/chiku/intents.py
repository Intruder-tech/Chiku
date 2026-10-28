"""Deterministic fast path for simple commands ("open Chrome", "pause the music").

Recognised locally with a regex so the action starts immediately, without a
round trip through the language model. Anything that doesn't match cleanly
falls through to the LLM, which can still call the same tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_PREFIX = (r"^\s*(?:(?:hey|hi|ok|okay)[,\s]+)?(?:chiku[,!.:\s]+)?"
           r"(?:(?:can|could|would|will) you\s+)?(?:please\s+)?")
_SUFFIX = r"(?:\s+(?:app|application|program|window))?(?:\s+for me)?(?:[,\s]+please)?\s*[.!?]*\s*$"

_OPEN = re.compile(
    _PREFIX + r"(?:open|launch|start|run|fire up|bring up)\s+(?:up\s+)?"
    r"(?:the\s+|my\s+)?(?P<target>.+?)" + _SUFFIX, re.I)
_SWITCH = re.compile(
    _PREFIX + r"(?:switch|go|change|take me)\s+(?:back\s+)?to\s+(?:the\s+|my\s+)?(?P<target>.+?)"
    + _SUFFIX, re.I)
_CLOSE = re.compile(
    _PREFIX + r"(?:close|quit|exit)\s+(?:the\s+|my\s+)?(?P<target>.+?)" + _SUFFIX, re.I)

# "stop" is deliberately absent: it is what people say to interrupt Chiku herself.
_MEDIA: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_PREFIX + r"(?:pause|resume|unpause)(?:\s+(?:the|my))?"
                r"(?:\s+(?:music|song|video|playback|media|track|spotify|youtube))?"
                r"(?:[,\s]+please)?\s*[.!?]*\s*$", re.I), "play_pause"),
    (re.compile(_PREFIX + r"(?:play|continue)(?:\s+(?:the|my))?\s+(?:music|song|playback|media)"
                r"(?:[,\s]+please)?\s*[.!?]*\s*$", re.I), "play_pause"),
    (re.compile(_PREFIX + r"(?:(?:next|skip(?:\s+(?:this|the))?)\s+(?:song|track)|skip)"
                r"(?:[,\s]+please)?\s*[.!?]*\s*$", re.I), "next_track"),
    (re.compile(_PREFIX + r"(?:previous|last|go back(?:\s+a)?)\s+(?:song|track)"
                r"(?:[,\s]+please)?\s*[.!?]*\s*$", re.I), "previous_track"),
    (re.compile(_PREFIX + r"(?:turn\s+(?:the\s+)?(?:volume|it|sound)\s+up|volume\s+up|"
                r"louder|turn\s+up\s+the\s+(?:volume|sound))"
                r"(?:\s+a\s+(?:bit|little))?(?:[,\s]+please)?\s*[.!?]*\s*$", re.I), "volume_up"),
    (re.compile(_PREFIX + r"(?:turn\s+(?:the\s+)?(?:volume|it|sound)\s+down|volume\s+down|"
                r"quieter|softer|turn\s+down\s+the\s+(?:volume|sound))"
                r"(?:\s+a\s+(?:bit|little))?(?:[,\s]+please)?\s*[.!?]*\s*$", re.I),
     "volume_down"),
    (re.compile(_PREFIX + r"(?:un)?mute(?:\s+(?:the\s+)?(?:sound|volume|audio|it))?"
                r"(?:[,\s]+please)?\s*[.!?]*\s*$", re.I), "mute"),
]

_DOMAIN = re.compile(r"^(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?$", re.I)
# Requests that start like "open ..." but aren't about launching an app.
_NOT_APP = re.compile(
    r"\b(tab|link|page|website|site|about|my mind|a new|and|then|with|in|to|on|"
    r"folder|document|eyes|door|sleep|bed)\b",
    re.I,
)

IntentKind = Literal["open_app", "open_url", "switch_app", "close_app", "media", "remember",
                     "forget"]

_FILLER = r"(?:(?:also|and|oh|btw|by the way|one more thing|actually)[,\s]+)*"
_REMEMBER = re.compile(
    r"^\s*" + _FILLER + _PREFIX[4:] + _FILLER + r"(?:please\s+)?"
    r"(?:remember|don'?t forget|keep in mind|note)\s+(?:that\s+)?(?P<fact>.{3,280}?)"
    r"\s*[.!]*\s*$", re.I)
_FORGET = re.compile(
    r"^\s*" + _FILLER + _PREFIX[4:] + _FILLER + r"(?:please\s+)?"
    r"forget\s+(?:that\s+|about\s+|what i (?:said|told you) about\s+)?(?P<fact>.{3,200}?)"
    r"\s*[.!]*\s*$", re.I)
_NOT_A_FACT = re.compile(r"^(?:to\s|when\s|what\s|how\s|it\b|this\b|everything\b|me\b)", re.I)


_KEEP_VERBS = {"can", "could", "will", "would", "should", "must", "might", "may", "was",
               "usually", "always", "never", "often", "really", "also", "don't", "didn't",
               "can't", "won't"}
_IRREGULAR = {"have": "has", "do": "does", "go": "goes", "am": "is"}


def _third_person(verb: str) -> str:
    low = verb.lower()
    if low in _IRREGULAR:
        return _IRREGULAR[low]
    if low in _KEEP_VERBS or not low.isalpha():
        return verb
    if re.search(r"(s|sh|ch|x|z|o)$", low):
        return verb + "es"
    if re.search(r"[^aeiou]y$", low):
        return verb[:-1] + "ies"
    return verb + "s"


def to_note(fact: str) -> str:
    """Turn the user's first-person phrasing into a note about the user.

    "I prefer tea" -> "User prefers tea"; "my sister is Asha" -> "User's sister is Asha".
    """
    s = fact.strip().rstrip(".!")
    s = re.sub(r"^(?:i'm|i am)\b", "User is", s, flags=re.I)
    m = re.match(r"^i\s+(\S+)(.*)$", s, flags=re.I)
    if m:
        s = f"User {_third_person(m.group(1))}{m.group(2)}"
    s = re.sub(r"^my\b", "User's", s, flags=re.I)
    s = re.sub(r"\bmy\b", "their", s, flags=re.I)
    s = re.sub(r"\bI'm\b", "they're", s)
    s = re.sub(r"\bI\b", "they", s)
    s = re.sub(r"\bme\b", "them", s, flags=re.I)
    return s[:1].upper() + s[1:]


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    target: str


def _app_target(m: re.Match[str] | None) -> str | None:
    if not m:
        return None
    target = m.group("target").strip(" .,!?\"'")
    if not target or len(target) > 60 or len(target.split()) > 4 or _NOT_APP.search(target):
        return None
    if target.lower().startswith(("a ", "an ", "some ", "something", "another ")):
        return None  # "switch to a different topic" is conversation, not an app
    return target


_EXPLICIT_SEARCH = re.compile(
    _PREFIX + r"(?:search(?:\s+(?:the\s+web|online|the\s+internet|google))?(?:\s+for)?|"
    r"look\s+up|google|find\s+out|check\s+online(?:\s+for)?)\s+(?P<q>.{2,200}?)\s*[.!?]*\s*$",
    re.I,
)
_FRESH = re.compile(
    r"\b(weather|forecast|rain(?:ing)?|temperature|news|headlines?|"
    r"latest|recent(?:ly)?|current(?:ly)?|"
    r"price|cost\s+of|stock|exchange\s+rate|score|who\s+won|winner|released?|launch(?:ed|es)?|"
    r"election|update[sd]?|happening|20[2-9]\d)\b",
    re.I,
)
# Questions about this computer are answered by local tools, never by a web search.
_LOCAL_TOPIC = re.compile(
    r"\b(my\s+(?:computer|laptop|pc|files?|folders?|documents|desktop|downloads|screen)|"
    r"battery|cpu|ram|memory|disk|storage|gpu|wifi|volume|apps?|applications?|programs?|"
    r"windows?\s+(?:open|running)|running|this\s+(?:computer|laptop|pc)|remind|timer|alarm|"
    r"music|song|track)\b",
    re.I,
)


def research_query(text: str) -> str | None:
    """Search query if the request clearly needs fresh information from the web."""
    m = _EXPLICIT_SEARCH.match(text)
    if m:
        return m.group("q").strip(" .,!?\"'")
    if _LOCAL_TOPIC.search(text) or not _FRESH.search(text) or len(text) < 8:
        return None
    q = re.sub(r"^\s*(?:(?:hey|hi|ok|okay)[,\s]+)?(?:chiku[,!.:\s]+)?", "", text, flags=re.I)
    return q.strip(" .!\"'")[:200] or None


def parse_command(text: str) -> Intent | None:
    m = _REMEMBER.match(text)
    if m and not _NOT_A_FACT.match(m.group("fact")):
        # "I'm vegetarian and I live in Pune" -> two separate memories, so each can be
        # forgotten on its own later.
        parts = re.split(r",?\s+and\s+(?=(?:i|i'm|my)\b)", m.group("fact"), flags=re.I)
        return Intent("remember", "\n".join(to_note(p) for p in parts if p.strip()))
    m = _FORGET.match(text)
    if m and not _NOT_A_FACT.match(m.group("fact")):
        return Intent("forget", m.group("fact").strip())
    for pattern, action in _MEDIA:
        if pattern.match(text):
            return Intent("media", action)
    m = _OPEN.match(text)
    if m:
        target = m.group("target").strip(" .,!?\"'")
        spoken_domain = re.sub(r"\s+dot\s+", ".", target, flags=re.I)
        if target and len(target) <= 60 and _DOMAIN.match(spoken_domain):
            return Intent("open_url", spoken_domain.lower())
        t = _app_target(m)
        return Intent("open_app", t) if t else None
    t = _app_target(_SWITCH.match(text))
    if t:
        return Intent("switch_app", t)
    t = _app_target(_CLOSE.match(text))
    if t:
        return Intent("close_app", t)
    return None

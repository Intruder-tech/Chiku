"""Deterministic fast path for simple commands ("open Chrome", "pause the music").

Recognised locally with a regex so the action starts immediately, without a
round trip through the language model. Anything that doesn't match cleanly
falls through to the LLM, which can still call the same tools.
"""

from __future__ import annotations

import re
import unicodedata
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
                     "forget", "install", "uninstall", "update", "check_updates"]

_INSTALL = re.compile(
    _PREFIX + r"(?:install|set up|download and install)\s+(?:the\s+|an?\s+)?(?:app\s+)?"
    r"(?P<target>.+?)(?:\s+(?:app|application|program))?(?:\s+for me)?(?:[,\s]+please)?"
    r"\s*[.!?]*\s*$", re.I)
_UNINSTALL = re.compile(
    _PREFIX + r"(?:uninstall|remove the (?:app|application|program))\s+(?:the\s+)?"
    r"(?P<target>.+?)(?:\s+(?:app|application|program))?(?:[,\s]+please)?\s*[.!?]*\s*$", re.I)
_CHECK_UPDATES = re.compile(
    _PREFIX + r"(?:check for (?:app |software )?updates|update (?:all )?(?:my |the )?"
    r"(?:apps|applications|programs|software)|(?:are there (?:any )?|any )(?:app )?updates"
    r"(?: available)?|install (?:the |all )?(?:latest |available )?updates)"
    r"(?:[,\s]+please)?\s*[.!?]*\s*$", re.I)
_UPDATE = re.compile(
    _PREFIX + r"update\s+(?:the\s+|my\s+)?(?P<target>.+?)(?:\s+(?:app|application))?"
    r"(?:[,\s]+please)?\s*[.!?]*\s*$", re.I)

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
    lang: str = "en"  # language the command was given in; acknowledgements use it


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
    """English commands first (Hindi patterns accept English loanwords like "next song"),
    then Hindi."""
    return _parse_english(text) or parse_hindi(text)


def _parse_english(text: str) -> Intent | None:
    m = _REMEMBER.match(text)
    if m and not _NOT_A_FACT.match(m.group("fact")):
        # "I'm vegetarian and I live in Pune" -> two separate memories, so each can be
        # forgotten on its own later.
        parts = re.split(r",?\s+and\s+(?=(?:i|i'm|my)\b)", m.group("fact"), flags=re.I)
        return Intent("remember", "\n".join(to_note(p) for p in parts if p.strip()))
    m = _FORGET.match(text)
    if m and not _NOT_A_FACT.match(m.group("fact")):
        return Intent("forget", m.group("fact").strip())
    if _CHECK_UPDATES.match(text):
        return Intent("check_updates", "")
    for pattern, kind in ((_UNINSTALL, "uninstall"), (_INSTALL, "install"), (_UPDATE, "update")):
        t = _app_target(pattern.match(text))
        if t:
            return Intent(kind, t)  # type: ignore[arg-type]
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


# -- Hindi (Devanagari as Whisper writes it, and romanised as people type it) -------------

_NUKTA = "़"


def _fold(text: str) -> str:
    # NFC writes nukta letters as base letter + nukta, however they were typed.
    return unicodedata.normalize("NFC", text)


def _hi(pattern: str) -> re.Pattern[str]:
    """Compile with an optional nukta after each letter that can take one, so words match
    whether or not the nukta was written (Whisper and people both vary)."""
    p = re.sub("([कखगजडढफ])", r"\1" + _NUKTA + "?",
               _fold(pattern).replace(_NUKTA, ""))
    return re.compile(p, re.I)


_HI_START = (r"^\s*(?:(?:hey|hi|ok|okay|arey?|अरे|हे)[,\s]+)?(?:(?:chiku|चिकू|चीकू)[,!.:।\s]+)?"
             r"(?:(?:please|zara|jara|प्लीज|ज़रा|जरा)\s+)?")
_HI_END = r"(?:\s+(?:please|plz|na|zara|प्लीज|ना|ज़रा|जरा))*\s*[.!?।]*\s*$"
_HI_OBJ = r"(?P<target>.+?)(?:\s+(?:app|ऐप|एप))?(?:\s+(?:ko|को))?"
_HI_MUSIC = r"(?:gaana|gana|gaane|music|song|गाना|गाने|गीत|म्यूजिक|संगीत|सॉन्ग)"
_HI_DO = r"(?:karo|kar\s+do|kardo|kijiye|करो|कर\s+दो|करिए|कीजिए)"

_HI_MEDIA: list[tuple[re.Pattern[str], str]] = [
    (_hi(_HI_START + _HI_MUSIC + r"\s+(?:roko|rok\s+do|band\s+" + _HI_DO + r"|pause\s+" + _HI_DO
         + r"|रोको|रोक\s+दो|बंद\s+" + _HI_DO + r"|बन्द\s+" + _HI_DO + r"|पॉज\s+" + _HI_DO + ")"
         + _HI_END), "play_pause"),
    (_hi(_HI_START + _HI_MUSIC + r"\s+(?:chalao|chala\s+do|bajao|baja\s+do|play\s+" + _HI_DO
         + r"|चलाओ|चला\s+दो|बजाओ|बजा\s+दो|प्ले\s+" + _HI_DO + ")" + _HI_END), "play_pause"),
    (_hi(_HI_START + r"(?:agla|next|अगला|नेक्स्ट)\s+" + _HI_MUSIC
         + r"(?:\s+(?:chalao|lagao|bajao|चलाओ|लगाओ|बजाओ))?" + _HI_END), "next_track"),
    (_hi(_HI_START + r"(?:pichhla|pichla|पिछला)\s+" + _HI_MUSIC
         + r"(?:\s+(?:chalao|lagao|bajao|चलाओ|लगाओ|बजाओ))?" + _HI_END), "previous_track"),
    (_hi(_HI_START + r"(?:awaaz|aawaz|awaz|awaj|volume|आवाज|वॉल्यूम)\s+(?:badhao|badha\s+do|"
         r"tez\s+" + _HI_DO + r"|बढ़ाओ|बढाओ|बढ़ा\s+दो|बढा\s+दो|तेज\s+" + _HI_DO + ")" + _HI_END),
     "volume_up"),
    (_hi(_HI_START + r"(?:awaaz|aawaz|awaz|awaj|volume|आवाज|वॉल्यूम)\s+(?:kam\s+" + _HI_DO
         + r"|ghatao|dheere\s+" + _HI_DO + r"|कम\s+" + _HI_DO + r"|घटाओ|धीमी\s+" + _HI_DO + ")"
         + _HI_END), "volume_down"),
    (_hi(_HI_START + r"(?:mute\s+" + _HI_DO + r"|म्यूट\s+" + _HI_DO + ")" + _HI_END), "mute"),
]
_HI_UNINSTALL = _hi(_HI_START + _HI_OBJ + r"\s+(?:uninstall\s+" + _HI_DO
                    + r"|अनइंस्टॉल\s+" + _HI_DO + r"|अनइंस्टाल\s+" + _HI_DO + ")" + _HI_END)
_HI_INSTALL = _hi(_HI_START + _HI_OBJ + r"\s+(?:install\s+" + _HI_DO + r"|इंस्टॉल\s+" + _HI_DO
                  + r"|इंस्टाल\s+" + _HI_DO + ")" + _HI_END)
_HI_OPEN = _hi(_HI_START + _HI_OBJ + r"\s+(?:kholo|khol\s+do|khol\s+de|kholiye|chalu\s+"
               + _HI_DO + r"|open\s+" + _HI_DO + r"|start\s+" + _HI_DO
               + r"|खोलो|खोल\s+दो|खोल\s+दे|खोलिए|खोलिये|चालू\s+" + _HI_DO + r"|ओपन\s+" + _HI_DO
               + r"|स्टार्ट\s+" + _HI_DO + ")" + _HI_END)
_HI_CLOSE = _hi(_HI_START + _HI_OBJ + r"\s+(?:band\s+" + _HI_DO + r"|बंद\s+" + _HI_DO
                + r"|बन्द\s+" + _HI_DO + ")" + _HI_END)
_HI_REMEMBER = [
    _hi(_HI_START + r"(?:yaad\s+rakh(?:na|o|iye|iyega)|याद\s+रख(?:ना|ो|िए|िये|िएगा))\s+"
        r"(?:ki|kee|कि|की)\s+(?P<fact>.{3,280}?)" + r"\s*[.!।]*\s*$"),
    _hi(_HI_START + r"(?P<fact>.{3,280}?)[,\s]+(?:(?:ye|yeh|ये|यह)\s+)?"
        r"(?:yaad\s+rakh(?:na|o|iye|iyega)|याद\s+रख(?:ना|ो|िए|िये|िएगा))" + _HI_END),
]
_HI_FORGET = _hi(_HI_START + r"(?P<fact>.{3,200}?)\s+(?:(?:wali|wala|वाली|वाला)\s+(?:baat|बात)\s+)?"
                 r"(?:bhool|bhul|भूल)\s+(?:jao|jaao|jaana|जाओ|जाना)" + _HI_END)


def _hi_target(m: re.Match[str] | None) -> str | None:
    if not m:
        return None
    target = m.group("target").strip(" .,!?\"'।")
    if not target or len(target) > 60 or len(target.split()) > 4:
        return None
    return target


def parse_hindi(text: str) -> Intent | None:
    """Hindi commands: "Chrome kholo", "गाना रोको", "आवाज़ बढ़ाओ", "याद रखना कि ..."."""
    t = _fold(text)
    for pattern in _HI_REMEMBER:
        m = pattern.match(t)
        if m:
            return Intent("remember", m.group("fact").strip(" ,"), "hi")
    m = _HI_FORGET.match(t)
    if m:
        return Intent("forget", m.group("fact").strip(" ,"), "hi")
    for pattern, action in _HI_MEDIA:
        if pattern.match(t):
            return Intent("media", action, "hi")
    for pattern, kind in ((_HI_UNINSTALL, "uninstall"), (_HI_INSTALL, "install"),
                          (_HI_OPEN, "open_app"), (_HI_CLOSE, "close_app")):
        target = _hi_target(pattern.match(t))
        if target:
            if kind == "open_app" and _DOMAIN.match(target):
                return Intent("open_url", target.lower(), "hi")
            return Intent(kind, target, "hi")  # type: ignore[arg-type]
    return None

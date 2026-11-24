"""Reflex: the instant tier, upgraded. Words in, a fixed list of actions out.

Idea from NUS Companion's reflex router (Apache-2.0): a decider that can only
CHOOSE (an intent from a fixed list, an app from the installed-app index, the
first and last word of a payload taken from what was actually said) and never
writes a reply, so it can never claim an action it did not take. Everything it
is unsure about goes to the brain unchanged.

decide(text, apps=...) -> list[Intent] (empty = hand it to the brain)

Order of deciders, cheapest first:
  1. the regex router on the whole utterance (router.parse)
  2. the same router on each clause, last first: real speech buries the command
     ("Yeah it just came. So can you... go and open my Spotify")
  3. short follow-ups to the last action ("higher", "a hundred", "again")
  4. optional: a local model that picks from the same intent list (classifier.py)

Guard rails (NUS added them after a transcription loop opened 43 browser
windows): at most MAX_ACTIONS per utterance, no repeated action inside one
utterance, and the same app or site is not relaunched within COOLDOWN_S.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .router import TOO_COMPLEX, Intent, normalize, parse

MAX_ACTIONS = 3
COOLDOWN_S = 8.0
FOLLOW_UP_S = 60.0

# Every reflex action is local and reversible. Nothing here sends, buys,
# deletes or types into a credential field; adding one is a policy change.
INTENTS: dict[str, str] = {
    "app_open": "open, launch or start a desktop app by name",
    "app_close": "close or quit a named app",
    "app_focus": "switch to or bring up an app that is already open",
    "open_site": "open a named website or web app (gmail, youtube, google calendar, x dot com)",
    "web_search": "search the web or google for something",
    "spotify_search": "play a specific song, artist, album or playlist on Spotify",
    "spotify_play": "start or resume music on Spotify with no specific song",
    "media_pause": "pause the music or media",
    "media_play": "resume the music or media",
    "media_next": "skip to the next song or track",
    "media_prev": "go back to the previous song or track",
    "volume_up": "turn the volume up or make it louder",
    "volume_down": "turn the volume down or make it quieter",
    "volume_set": "set the volume to a number",
    "mute": "mute the sound",
    "unmute": "unmute the sound",
    "window_close": "close the current window or tab's window",
    "window_min": "minimize or hide the current window",
    "window_max": "maximize or make the current window full screen",
    "snap_left": "snap the current window to the left half of the screen",
    "snap_right": "snap the current window to the right half of the screen",
    "new_tab": "open a new empty browser tab",
    "close_tab": "close the current browser tab",
    "next_tab": "switch to the next browser tab",
    "screenshot": "take a screenshot",
    "type_text": "type given words into the app that is in front",
    "time": "say what time it is",
    "stop": "stop talking or cancel",
    "thanks": "thank or dismiss the assistant",
    "again": "repeat the last action",
    "none": "chit-chat, a question, thinking out loud, or anything not on this list",
}

# A clause cut off mid-thought is never acted on ("could you open up my...").
TRAILING_OFF = re.compile(r"(?:\.\.\.|…|\b(?:my|the|a|an|and|uh|um|of|for|with|open up|open))\s*$", re.I)
CLAUSE_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*(?:\.\.\.|…)\s*|\s+(?:and then|then|also)\s+", re.I)
QUESTION = re.compile(r"^(?:what|who|when|why|which|where|how|is|are|do|does|did|should|would|will|explain|tell me|define)\b(?!.*\b(?:open|close|play|pause|skip|volume|mute)\b)")

# Short follow-ups that only mean something right after a related action.
MORE = re.compile(r"^(?:(?:even |a (?:bit|little|lot) )?(?:higher|louder|more|up|again|turn it up|crank it|bump it up)|do it (?:higher|louder|again)|higher|louder)$")
LESS = re.compile(r"^(?:(?:even |a (?:bit|little|lot) )?(?:lower|quieter|softer|less|down|turn it down)|do it (?:lower|quieter))$")
LEVEL = re.compile(r"^(?:(?:do |make it |put it at |set it to |go to |to |at )?(?:the |a )?(\d{1,3}|hundred|max|full|fifty|sixty|seventy|eighty|ninety|forty|thirty|twenty|ten)(?: percent|%)?)$")
# Interjections people lead a follow-up with ("yo", "oh my gosh, just", "nah").
LEAD_IN = re.compile(r"^(?:(?:oh my gosh|oh my god|come on|i said|okay|yeah|yo|oh|omg|bro|dude|nah|no|yes|ok|just|like|bruh|man|dang)\s+)+")
# Command verbs: where a buried command starts ("I said open up my gmail", "I mean, open Claude").
VERB_AT = re.compile(r"\b(?=(?:open|launch|start|pull up|bring up|fire up|switch to|go to|close|quit|play|put on|pause|resume|skip|"
                     r"turn (?:it|the|up|down)|raise|lower|mute|unmute|set the volume|volume|snap|move it|google|search|"
                     r"take a screenshot|screenshot|new tab|next tab|maximi[sz]e|minimi[sz]e)\b)")
AGAIN = re.compile(r"^(?:again|do (?:it|that) again|one more time|same thing|repeat that)$")
NEXT_ONE = re.compile(r"^(?:next(?: one)?|another one|skip (?:it|again))$")
LEVEL_WORDS = {"hundred": 100, "a hundred": 100, "max": 100, "full": 100, "ninety": 90, "eighty": 80,
               "seventy": 70, "sixty": 60, "fifty": 50, "forty": 40, "thirty": 30, "twenty": 20, "ten": 10}

VOLUME = {"volume_up", "volume_down", "volume_set", "mute", "unmute"}
MEDIA = {"media_next", "media_prev", "media_play", "media_pause", "spotify_play", "spotify_search"}
LAUNCHES = {"app_open", "open_site"}


def clauses(text: str) -> list[str]:
    """Spoken sentence pieces, cleaned, in order. Two app opens joined by "and" are split."""
    out: list[str] = []
    for piece in CLAUSE_SPLIT.split(text or ""):
        piece = piece.strip(" ,")
        if not piece:
            continue
        out.extend(_split_and(piece))
    return out


def _anchored(piece: str) -> Intent | None:
    """Parse from the first command verb on, for commands behind a lead-in ("I said open up my gmail").
    Questions never count ("how do I open chrome")."""
    t = normalize(piece)
    if not t or QUESTION.match(t) or TOO_COMPLEX.search(t):
        return None
    for m in VERB_AT.finditer(t):
        if m.start() == 0:
            continue  # the whole clause was already tried
        intent = parse(t[m.start():])
        if intent is not None:
            return intent
    return None


# A second clause that is its own command, not a second app to open.
SECOND_COMMAND = re.compile(r"^(?:turn|set|mute|unmute|skip|pause|resume|minimize|maximize|snap|"
                            r"volume|next|previous|go back|take a screenshot)\b", re.I)


def _split_and(piece: str) -> list[str]:
    """"open Claude and my Google Chrome" -> two clauses with the verb carried over."""
    m = re.match(r"^(.*?\b(?:open|launch|start|close|quit)\b(?: up)?)\s+(.+?)\s+and\s+(?:also\s+)?(?:my\s+|the\s+)?(.+)$", piece, re.I)
    if not m or re.search(r"\b(?:type|search|play|send|write|tell)\b", m.group(3), re.I):
        return [piece]
    if SECOND_COMMAND.match(m.group(3)):
        return [f"{m.group(1)} {m.group(2)}", m.group(3)]   # "open discord and turn the volume down"
    verb = re.sub(r"^.*?\b(open|launch|start|close|quit)\b", r"\1", m.group(1), flags=re.I)
    return [f"{m.group(1)} {m.group(2)}", f"{verb} {m.group(3)}"]


@dataclass
class Reflex:
    """Decides; does not act. Holds the little state follow-ups and cooldowns need."""
    classify: Callable[[str], Intent | None] | None = None   # optional local model (classifier.py)
    now: Callable[[], float] = time.monotonic
    last: Intent | None = None
    last_at: float = 0.0
    launched: dict[str, float] = field(default_factory=dict)

    def decide(self, text: str) -> list[Intent]:
        intents = self._raw(text)
        return self._guard(intents)

    # -- deciders ----------------------------------------------------------
    def _raw(self, text: str) -> list[Intent]:
        pieces = clauses(text)
        if not pieces:
            return []
        if len(pieces) == 1 and not TRAILING_OFF.search(text.strip()):
            whole = parse(text) or _anchored(text)
            if whole is not None:
                return [whole]
        elif len(pieces) > 1:
            found: list[Intent] = []
            for piece in pieces:
                if TRAILING_OFF.search(piece):
                    continue
                intent = parse(piece) or _anchored(piece)
                if intent is not None and intent.name != "thanks":
                    found.append(intent)
            # Chatter around a command: act on the commands, ignore the rest.
            if found:
                return found
        follow = self._follow_up(LEAD_IN.sub("", normalize(pieces[-1])))
        if follow is not None:
            return [follow]
        if self.classify is not None:
            target = pieces[-1] if pieces else text
            if target and not TRAILING_OFF.search(target) and not QUESTION.match(normalize(target)):
                picked = self.classify(target)
                if picked is not None:
                    return [picked]
        return []

    def _follow_up(self, t: str) -> Intent | None:
        if self.last is None or self.now() - self.last_at > FOLLOW_UP_S or not t:
            return None
        family = self.last.name
        if family in VOLUME:
            m = LEVEL.match(t)
            if m:
                word = m.group(1)
                level = int(word) if word.isdigit() else LEVEL_WORDS.get(word)
                if level is not None and 0 <= level <= 100:
                    return Intent("volume_set", level)
            if MORE.match(t):
                return Intent("volume_up", 10)
            if LESS.match(t):
                return Intent("volume_down", 10)
        if family in MEDIA and NEXT_ONE.match(t):
            return Intent("media_next")
        if AGAIN.match(t) and family not in ("stop", "thanks", "time"):
            return self.last
        return None

    # -- guard rails -------------------------------------------------------
    def _guard(self, intents: list[Intent]) -> list[Intent]:
        out: list[Intent] = []
        seen: set[Intent] = set()
        for intent in intents:
            if intent in seen or len(out) >= MAX_ACTIONS:
                continue
            seen.add(intent)
            out.append(intent)
        return out

    def recently_launched(self, key: str) -> bool:
        """True if this app or site was launched within the cooldown (focus it instead)."""
        at = self.launched.get(key.lower())
        return at is not None and self.now() - at < COOLDOWN_S

    def note(self, intent: Intent) -> None:
        """Called by the caller after an intent actually ran."""
        self.last, self.last_at = intent, self.now()
        if intent.name in LAUNCHES and intent.arg:
            self.launched[str(intent.arg).lower()] = self.now()

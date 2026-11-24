"""Optional local decider for the reflex tier: a small Ollama model that can only choose.

The model gets the words of one clause, numbered, and must answer with JSON that
Ollama's structured output constrains to:
    {"intent": <one of the fixed intents>, "start": int, "end": int, "confidence": 0..1}
The payload (an app name, a search, a song) is cut from the user's own words by
those indices, so the model cannot invent a target, and it never writes a reply,
so it cannot claim that something happened. Anything below the confidence bar,
"none", or a slow or missing Ollama means: hand the turn to the brain.

Only reversible intents can come from here. Closing things and typing stay with
the regex rules and the brain.
"""
from __future__ import annotations

import json
import re
import urllib.request

from .router import Intent, site_url

ALLOWED = {
    "app_open": "open, launch or start a desktop app (the words name the app)",
    "app_focus": "switch to an app that is already open (the words name the app)",
    "open_site": "open a website or web app (the words name the site)",
    "web_search": "search the web or google (the words are the search)",
    "spotify_search": "play a specific song, artist, album or playlist (the words name it)",
    "spotify_play": "start or resume music with no specific song",
    "media_pause": "pause the music",
    "media_next": "skip to the next song",
    "media_prev": "go back to the previous song",
    "volume_up": "turn the volume up or make it louder",
    "volume_down": "turn the volume down or make it quieter",
    "volume_set": "set the volume to a number (the words are the number)",
    "mute": "mute the sound",
    "snap_left": "snap the window to the left half",
    "snap_right": "snap the window to the right half",
    "new_tab": "open a new browser tab",
    "screenshot": "take a screenshot",
    "none": "a question, chit-chat, thinking out loud, an unfinished thought, or anything else",
}
NEEDS_WORDS = {"app_open", "app_focus", "open_site", "web_search", "spotify_search", "volume_set"}
MIN_CONFIDENCE = 0.8
NUMBER = re.compile(r"\d{1,3}")
WORD_LEVELS = {"hundred": 100, "max": 100, "full": 100, "ninety": 90, "eighty": 80, "seventy": 70,
               "sixty": 60, "fifty": 50, "forty": 40, "thirty": 30, "twenty": 20, "ten": 10}

SYSTEM = (
    "You route one spoken command for a Windows voice assistant. Choose exactly one intent from the list. "
    "If the command needs words (an app, a site, a search, a song, a number), give the index of the first "
    "and last of those words from the numbered list; otherwise start and end are -1. "
    "Choose none for questions, chit-chat, unfinished sentences, or anything you are not sure about. "
    "Never guess: a low confidence is fine.\nIntents:\n"
    + "\n".join(f"- {k}: {v}" for k, v in ALLOWED.items())
)
SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(ALLOWED)},
        "start": {"type": "integer"},
        "end": {"type": "integer"},
        "confidence": {"type": "number"},
    },
    "required": ["intent", "start", "end", "confidence"],
}


class LocalClassifier:
    def __init__(self, model: str = "qwen3.5:4b", host: str = "http://127.0.0.1:11434",
                 timeout_s: float = 1.5) -> None:
        self.model, self.host, self.timeout_s = model, host.rstrip("/"), timeout_s
        self.available: bool | None = None   # None = not checked yet

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        req = urllib.request.Request(self.host + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def warm(self) -> None:
        """Load the model in the background at startup; marks Ollama missing if it doesn't answer."""
        try:
            self._post("/api/generate", {"model": self.model, "prompt": "", "keep_alive": "30m"}, 30)
            self.available = True
        except Exception:
            self.available = False

    def __call__(self, clause: str) -> Intent | None:
        if self.available is False:
            return None
        words = clause.split()
        if not words or len(words) > 25:
            return None
        numbered = " ".join(f"[{i}]{w}" for i, w in enumerate(words))
        try:
            out = self._post("/api/chat", {
                "model": self.model, "stream": False, "format": SCHEMA, "think": False, "keep_alive": "30m",
                "options": {"temperature": 0, "num_predict": 60},
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": numbered}],
            }, self.timeout_s)
            pick = json.loads(out["message"]["content"])
        except Exception:
            return None
        return to_intent(pick, words)


def to_intent(pick: dict, words: list[str]) -> Intent | None:
    """Turn the model's choice into an Intent, or None. Pure, so it is tested without a model."""
    intent = pick.get("intent")
    try:
        confidence = float(pick.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    if intent not in ALLOWED or intent == "none" or confidence < MIN_CONFIDENCE:
        return None
    if intent not in NEEDS_WORDS:
        return Intent(intent)
    start, end = pick.get("start"), pick.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end < len(words):
        return None
    payload = re.sub(r"[^\w\s'.-]", "", " ".join(words[start:end + 1])).strip()
    if not payload:
        return None
    if intent == "volume_set":
        m = NUMBER.search(payload)
        level = int(m.group()) if m else WORD_LEVELS.get(payload.lower().split()[-1])
        return Intent("volume_set", level) if level is not None and 0 <= level <= 100 else None
    if intent == "open_site":
        url = site_url(payload.lower())
        return Intent("open_site", url) if url else None
    return Intent(intent, payload.lower() if intent in ("app_open", "app_focus") else payload)

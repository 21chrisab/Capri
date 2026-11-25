"""Tier 1 router: instant commands matched by regex, no AI.

parse(text) -> Intent | None. Pure and testable. actions.py does the work.
Anything that returns None falls through to the brain (M3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    name: str
    arg: str | int | None = None


FILLER = re.compile(r"^(?:(?:hey|yo|ok|okay|yeah|so|alright|and|um|uh)\s+)*(?:capri[,\s]+)?"
                    r"(?:(?:can|could|would) you(?: also)?\s+|please\s+|go ahead and\s+|go and\s+|let's\s+|just\s+|also\s+)*", re.I)
INNER_FILLER = re.compile(r"\b(?:uh|um|uhh|umm|like)\b\s*")
TRAILING = re.compile(r"\s*(?:please|for me|thanks|thank you|real quick|rn|now)\s*$", re.I)
NUM_WORDS = {"zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
             "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "a hundred": 100, "max": 100, "full": 100}
MUSIC = r"(?:the\s+)?(?:music|song|track|spotify|it)"


def normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"(?<=\w)\.(?=(?:com|org|net|io|ai|dev|app|edu|gov|co)\b)", " dot ", t)  # "x.com" survives
    t = re.sub(r"[^\w\s%']", " ", t)
    t = INNER_FILLER.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = FILLER.sub("", t)
    for _ in range(2):
        t = TRAILING.sub("", t)
    return t.strip()


# Web apps people open by name. Installed desktop apps (Outlook, Spotify, Claude, ChatGPT, Discord)
# are deliberately absent: those open as apps.
SITES = {
    "gmail": "https://mail.google.com", "email": "https://mail.google.com", "my email": "https://mail.google.com",
    "youtube": "https://www.youtube.com", "google calendar": "https://calendar.google.com",
    "calendar": "https://calendar.google.com", "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com", "google docs": "https://docs.google.com",
    "docs": "https://docs.google.com", "google sheets": "https://sheets.google.com",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com",
    "google": "https://www.google.com", "github": "https://github.com", "reddit": "https://www.reddit.com",
    "twitter": "https://x.com", "x": "https://x.com", "instagram": "https://www.instagram.com",
    "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.com", "canvas": "https://utexas.instructure.com",
    "chat gpt website": "https://chatgpt.com", "claude website": "https://claude.ai", "tiktok": "https://www.tiktok.com",
    "linkedin": "https://www.linkedin.com", "wikipedia": "https://www.wikipedia.org", "twitch": "https://www.twitch.tv",
}
GENERIC_PLAY = {"music", "some music", "something", "a song", "any song", "a random song", "some songs", "some songs", "spotify", "my music", "anything",
                "my playlist", "my liked songs", "liked songs", "it", "that"}


def site_url(name: str) -> str | None:
    """A URL for a named web app or a spoken domain ("x dot com"), else None."""
    name = re.sub(r"^(?:the|my)\s+", "", name.strip())
    name = re.sub(r"\s+(?:website|site|page|tab)$", "", name)
    if name in SITES:
        return SITES[name]
    m = re.fullmatch(r"([a-z0-9-]+(?: [a-z0-9-]+)?) dot (com|org|net|io|ai|dev|app|edu|gov|co)", name)
    if m:
        return f"https://{m.group(1).replace(' ', '')}.{m.group(2)}"
    return None


def _open(m) -> Intent | None:
    target = m.group(1)
    if re.search(r"\band\b", target):
        return None  # two things: the reflex splits it into clauses
    url = site_url(target)
    if url is not None:
        return Intent("open_site", url)
    if re.fullmatch(r"(?:my|the|a|up|it|this|that)?", target):
        return None
    return Intent("app_open", target)


def _play(m) -> Intent | None:
    q = re.sub(r"\s+(?:on|in|from) spotify$", "", m.group(1)).strip()
    # "play any song or change the song to the table": the specific one wins.
    q = re.split(r"\bor (?:change (?:the|it) (?:song|music|track) to|play|put on)\s+", q)[-1].strip()
    if not q or q in GENERIC_PLAY:
        return Intent("spotify_play")
    return Intent("spotify_search", q)


def _num(s: str) -> int | None:
    s = s.strip().rstrip("%").replace(" percent", "").strip()
    if s.isdigit():
        return max(0, min(100, int(s)))
    return NUM_WORDS.get(s)


# (pattern, builder). First match wins, so specific patterns come first.
RULES: list[tuple[re.Pattern, callable]] = [
    (re.compile(r"^(?:stop|stop talking|shut up|be quiet|never ?mind|nevermind|cancel|quiet|hush|nvm)$"),
     lambda m: Intent("stop")),
    (re.compile(r"^(?:thanks|thank you|thx|ty|appreciate it|good job|nice)(?: capri)?$"),
     lambda m: Intent("thanks")),
    (re.compile(r"^(?:what time is it|what's the time|whats the time|time|what is the time)$"),
     lambda m: Intent("time")),

    # Workflow modes. Needs "mode"/"workflow" so "switch to spotify" stays an app switch.
    (re.compile(r"^(?:switch to|go to|enter|set|activate|use|turn on) (?:the )?"
                r"(general|coding|code|assignments?|homework|content studio|studio)(?: mode| workflow)$"),
     lambda m: Intent("set_mode", {"code": "coding", "assignment": "assignments", "homework": "assignments",
                                   "studio": "content_studio"}.get(m.group(1), m.group(1).replace(" ", "_")))),

    # Life OS (life/ folder in Google Drive). Before the window rules: "open my dashboard" isn't an app.
    (re.compile(r"^(?:open|show|pull up|bring up) (?:me )?(?:my |the )?(?:life )?dashboard$"),
     lambda m: Intent("life_dashboard")),
    (re.compile(r"^(?:what's|what is|whats) (?:on )?(?:my plate|my list|today|the list|my top (?:3|three))(?: today| for today)?$"
                r"|^what do i (?:have|need) to do today$|^what(?:'s| is) my (?:plan|day)(?: look like)?(?: for)?(?: today)?$"),
     lambda m: Intent("life_today")),
    (re.compile(r"^(?:add|put|throw) (.+?) (?:to|on|in|into) (?:my |the )?(?:inbox|to ?do(?:s| list)?|list|task list)$"
                r"|^(?:note to self|jot down|capture)(?: that)? (.+)$"),
     lambda m: Intent("life_capture", (m.group(1) or m.group(2)).strip())),
    (re.compile(r"^(?:mark|check off|cross off) (.+?)(?: as)?(?: done| complete| finished)?$"
                r"|^i (?:finished|completed) (.+)$"),
     lambda m: Intent("life_done", (m.group(1) or m.group(2)).strip())),

    # Spotify / media
    (re.compile(rf"^(?:skip|next)(?: {MUSIC}| one)?$|^(?:next|skip) (?:song|track)$|^skip this(?: song| track)?$"),
     lambda m: Intent("media_next")),
    (re.compile(rf"^(?:previous|go back|last|back)(?: {MUSIC}| one)?$|^(?:play the )?(?:previous|last) (?:song|track)$"),
     lambda m: Intent("media_prev")),
    (re.compile(rf"^(?:pause|stop)(?: {MUSIC})$|^pause$"),
     lambda m: Intent("media_pause")),
    (re.compile(rf"^(?:resume|unpause|continue)(?: {MUSIC})?$"),
     lambda m: Intent("media_play")),
    (re.compile(r"^(?:open |start |launch )?spotify(?: and)? play(?: (?:some )?music| something| my (?:music|liked songs|playlist))?$"
                r"|^play (?:some )?(?:music|spotify|my music|something)(?: on spotify)?$|^play$"),
     lambda m: Intent("spotify_play")),

    # Volume
    (re.compile(r"^(?:mute|mute (?:it|the volume|volume|sound|audio))$"), lambda m: Intent("mute")),
    (re.compile(r"^unmute(?: (?:it|the volume|volume|sound|audio))?$"), lambda m: Intent("unmute")),
    (re.compile(r"^(?:set |put |turn )?(?:the )?(?:volume|sound) (?:to |at )?(\d{1,3}%?|\d{1,3} percent|[a-z ]+?)(?: percent)?$"),
     lambda m: Intent("volume_set", _num(m.group(1))) if _num(m.group(1)) is not None else None),
    (re.compile(r"^(?:turn (?:it|the volume|the music|the sound) up|volume up|louder|turn up(?: the volume| the music)?"
                r"|(?:make it )?louder|raise the volume|crank it(?: up)?)(?: a (?:little|bit|lot))?$"),
     lambda m: Intent("volume_up", 30 if "lot" in m.group(0) or "crank" in m.group(0) else 10)),
    (re.compile(r"^(?:turn (?:it|the volume|the music|the sound) down|volume down|quieter|turn down(?: the volume| the music)?"
                r"|(?:make it )?quieter|lower the volume|softer)(?: a (?:little|bit|lot))?$"),
     lambda m: Intent("volume_down", 30 if "lot" in m.group(0) else 10)),

    # Tabs (before "close this window" so "close this tab" closes only the tab)
    (re.compile(r"^(?:close|kill) (?:this|the|current|that) tab$|^close tab$"), lambda m: Intent("close_tab")),
    (re.compile(r"^(?:open |make )?(?:a )?new (?:empty )?tab$"), lambda m: Intent("new_tab")),
    (re.compile(r"^(?:next tab|switch tabs?|go to the next tab)$"), lambda m: Intent("next_tab")),
    (re.compile(r"^(?:snap|move|put) (?:it |this |that |the window |this window |that window )?(?:to (?:the )?)?(left|right)(?: side| half)?$"),
     lambda m: Intent("snap_" + m.group(1))),
    (re.compile(r"^(?:take )?(?:a )?(?:screenshot|screen shot)(?: of (?:the|my) screen)?$"), lambda m: Intent("screenshot")),
    (re.compile(r"^(?:google|search(?: up| for)?|search google for|look up on google) (.+)$"),
     lambda m: Intent("web_search", m.group(1)) if not re.match(r"(?:my |the )?(?:files?|computer|pc|folder)", m.group(1)) else None),
    (re.compile(r"^(?:play|put on|throw on|queue up|play me) (.+)$|^change the (?:song|music|track) to (.+)$"),
     lambda m: _play(type("M", (), {"group": lambda self, i: m.group(1) or m.group(2)})())),

    # Windows
    (re.compile(r"^(?:close|quit|exit|kill) (?:this|the|current|that)(?: window| app)?$|^close (?:it|window)$"),
     lambda m: Intent("window_close")),
    (re.compile(r"^(?:minimi[sz]e|hide)(?: (?:this|the|that|current))?(?: window| app)?$"),
     lambda m: Intent("window_min")),
    (re.compile(r"^(?:maximi[sz]e|full ?screen|make (?:it|this) (?:full ?screen|bigger))(?: (?:this|the|that|current))?(?: window| app)?$"),
     lambda m: Intent("window_max")),
    (re.compile(r"^(?:close|quit|exit|kill) (?:the |my )?(.+?)(?: app| window)?$"),
     lambda m: Intent("app_close", m.group(1))),
    (re.compile(r"^(?:go to|pull up|bring up|open|load) (?:up )?(.+? dot (?:com|org|net|io|ai|dev|app|edu|gov|co))$"),
     lambda m: Intent("open_site", site_url(m.group(1))) if site_url(m.group(1)) else None),
    (re.compile(r"^(?:switch to|go to|bring up|focus|show me|show) (?:the |my )?(.+?)(?: app| window)?$"),
     lambda m: (Intent("open_site", site_url(m.group(1))) if site_url(m.group(1)) and m.group(1) not in ("google",)
                else Intent("app_focus", m.group(1)))),
    (re.compile(r"^(?:open|launch|start|run|pull up|fire up|boot up) (?:up )?(?:the |my )?(.+?)(?: app)?$"), _open),
]

# Phrases that look like commands but carry more than a tier-1 command can handle.
TOO_COMPLEX = re.compile(r"\b(?:and then|after that|and also|then)\b|\band (?:send|type|search|write|find|tell)\b")


TYPE_RAW = re.compile(r"^\W*(?:(?:hey|ok|okay|yo)\W+)?(?:capri\W+)?(?:(?:can|could|would) you\s+|please\s+)*"
                      r"(?:type|type out|write out)\s*[:,]?\s+(.+?)\s*$", re.I)


def parse(text: str) -> Intent | None:
    raw = TYPE_RAW.match(text or "")
    if raw:
        payload = re.sub(r"\s+(?:please|for me)\W*$", "", raw.group(1), flags=re.I).strip().strip('"')
        return Intent("type_text", payload) if payload else None
    t = normalize(text)
    if not t or TOO_COMPLEX.search(t):
        return None
    for pat, build in RULES:
        m = pat.match(t)
        if m:
            intent = build(m)
            if intent is not None:
                return intent
    return None

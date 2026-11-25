"""Runs router intents. Ack first (pre-rendered, ~0 ms), act right after.

execute() returns False when it can't handle the intent, so the caller can
fall through to the brain (M3) instead of failing loudly.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time
import urllib.parse
from typing import TYPE_CHECKING

from . import sysctl
from .router import Intent
from .sysapps import AppIndex, launch

if TYPE_CHECKING:
    from .timing import TurnTimer
    from .voice import Voice

SPOTIFY = "Spotify.exe"


def spotify_playing() -> bool | None:
    """Spotify's window title is 'Artist - Song' while playing, 'Spotify ...' when paused.
    None = not running."""
    if hasattr(sysctl, "spotify_playing"):
        return sysctl.spotify_playing()
    for _, title, proc in sysctl.top_windows():
        if proc.lower() == SPOTIFY.lower():
            return not title.lower().startswith("spotify")
    return None


def spotify_play(apps: AppIndex) -> str:
    """Open Spotify if needed, then play. Never toggles music off by accident."""
    state = spotify_playing()
    if state is True:
        return "already playing"
    if state is None:
        app = apps.find("spotify")
        if app is None:
            return "Spotify isn't installed"
        launch(app)
        if sysctl.wait_for_window("spotify", 10) is None:
            return "Spotify didn't open"
        time.sleep(1.5)  # the player needs a moment before it accepts media keys
    if spotify_playing() is not True:
        sysctl.media_play_pause()
    return "playing"


def open_url(url: str) -> None:
    os.startfile(url)  # the default browser; a new tab if it is already open


def spotify_search_play(query: str, wait_s: float = 12.0) -> bool:
    """Search Spotify for query and press the best-matching "Play ..." button (UI Automation)."""
    import uiautomation as auto

    os.startfile("spotify:search:" + urllib.parse.quote(query))
    words = [w for w in query.lower().split() if len(w) > 2 and w not in ("the", "and", "by", "song")]
    need = max(1, (len(words) + 1) // 2)
    deadline = time.time() + wait_s
    time.sleep(1.0)
    while time.time() < deadline:
        for hwnd, _, proc in sysctl.top_windows():
            if proc.lower() != SPOTIFY.lower():
                continue
            best = None
            for ctrl, _depth in auto.WalkControl(auto.ControlFromHandle(hwnd), maxDepth=40):
                if ctrl.ControlTypeName != "ButtonControl":
                    continue
                name = (ctrl.Name or "").lower()
                if not name.startswith("play ") or name in ("play", "play/pause"):
                    continue
                score = sum(w in name for w in words)
                if best is None or score > best[0]:
                    best = (score, ctrl)
            # Until the search page loads, the home page's own "Play ..." buttons would play the wrong thing.
            if best is not None and best[0] >= need:
                try:
                    best[1].GetInvokePattern().Invoke()
                except Exception:
                    best[1].Click(simulateMove=False)
                return True
        time.sleep(0.5)
    return False


def web_search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


class Actions:
    def __init__(self, voice: "Voice", apps: AppIndex, life=None, hands=None, memory=None) -> None:
        self.voice = voice
        self.apps = apps
        self.life = life          # capri.life.Life, or None when the life folder isn't set up
        self.hands = hands
        self.memory = memory      # capri.memory.Memory, for workflow-mode switches

    def execute(self, intent: Intent, timer: "TurnTimer", quiet: bool = False) -> bool:
        started = threading.Event()

        def ack(text: str = "Got it.") -> None:
            if quiet:          # the second command of one sentence: the first already acked
                started.set()
                return
            self.voice.ack(text, started)

        def say(text: str) -> None:
            self.voice.say(text, started)

        n, arg = intent.name, intent.arg
        if n == "stop":
            self.voice.stop()
            timer.mark("action_done")
            return True
        if n == "set_mode":
            if self.memory is None:
                return False
            say(self.memory.set_mode(str(arg)))
            timer.mark("action_done")
            return True
        if n.startswith("life_"):
            if self.life is None:
                return False      # no life folder: let the brain explain
            if n == "life_capture":
                ack("Got it.")
                self.life.capture(str(arg))
            elif n == "life_today":
                say(self.life.today_summary())
            elif n == "life_done":
                result = self.life.done(str(arg))
                if result.startswith("Checked off"):
                    ack("Done.")
                else:
                    say(result)
            elif n == "life_dashboard":
                ack("Yep.")
                threading.Thread(target=self.life.dashboard, name="dashboard", daemon=True).start()
            else:
                return False
        elif n == "thanks":
            ack("Anytime.")
        elif n == "time":
            say(f"It's {dt.datetime.now().strftime('%I:%M').lstrip('0')}.")
        elif n == "app_open":
            app = self.apps.find(str(arg))
            if app is None:
                say(f"I couldn't find {arg}.")
            else:
                ack("On it.")
                launch(app)
        elif n == "app_focus":
            hwnd = sysctl.find_window(str(arg))
            if hwnd is None:
                return False  # "show me the weather" isn't a window; let the brain take it
            ack("Yep.")
            sysctl.focus(hwnd)
        elif n == "app_close":
            hwnd = sysctl.find_window(str(arg))
            if hwnd is None:
                say(f"{arg} isn't open.")
            else:
                ack("Done.")
                sysctl.close(hwnd)
        elif n in ("window_close", "window_min", "window_max"):
            hwnd = sysctl.foreground()
            ack("Done.")
            {"window_close": sysctl.close, "window_min": sysctl.minimize, "window_max": sysctl.maximize}[n](hwnd)
        elif n == "open_site":
            ack("On it.")
            open_url(str(arg))
        elif n == "web_search":
            ack("On it.")
            open_url(web_search_url(str(arg)))
        elif n == "spotify_search":
            ack("Coming right up.")
            query = str(arg)

            def play() -> None:
                try:
                    if not spotify_search_play(query):
                        self.voice.say("Couldn't find that one on Spotify.")
                except Exception as e:  # never let a UI hiccup kill the turn
                    print(f"\r[spotify search] {e!r}", flush=True)
                    self.voice.say("Spotify didn't cooperate on that one.")
            threading.Thread(target=play, name="spotify-search", daemon=True).start()
        elif n in ("snap_left", "snap_right"):
            ack("Done.")
            sysctl.snap(n.split("_")[1])
        elif n in ("new_tab", "close_tab", "next_tab"):
            ack("Yep.")
            {"new_tab": sysctl.new_tab, "close_tab": sysctl.close_tab, "next_tab": sysctl.next_tab}[n]()
        elif n == "screenshot":
            sysctl.screenshot()
            ack("Got it, it's in your Screenshots folder.")
        elif n == "type_text":
            if self.hands is None:
                return False
            result = self.hands.type_text(str(arg))
            if result.startswith("Refused"):
                say("That looks like a password field, so you'll have to type that one.")
            else:
                started.set()   # the typing itself is the feedback
        elif n == "spotify_play":
            ack("Coming right up.")
            result = spotify_play(self.apps)
            if result in ("Spotify isn't installed", "Spotify didn't open"):
                self.voice.say(result + ".")
        elif n == "media_play":
            ack("Yep.")
            if spotify_playing() is not True:
                sysctl.media_play_pause()
        elif n == "media_pause":
            ack("Yep.")
            if spotify_playing() is not False:
                sysctl.media_play_pause()
        elif n in ("media_next", "media_prev"):
            ack("Yep.")
            (sysctl.media_next if n == "media_next" else sysctl.media_prev)()
        elif n == "volume_up":
            sysctl.volume_up(int(arg or 10)); ack("Yep.")
        elif n == "volume_down":
            sysctl.volume_down(int(arg or 10)); ack("Yep.")
        elif n == "volume_set":
            sysctl.volume_set(int(arg)); ack("Done.")
        elif n == "mute":
            sysctl.mute_toggle()
            started.set()  # nothing to hear once muted
        elif n == "unmute":
            sysctl.mute_toggle()
            ack("Yep.")
        else:
            return False
        timer.mark("action_done")
        if started.wait(5):
            timer.mark("first_audio")
        return True

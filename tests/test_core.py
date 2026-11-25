"""Unit tests for the parts that don't need Windows, a mic or models."""
import json
import threading

from capri.gen import Generation
from capri.timing import TurnTimer, pct, report


def test_generation_cancels_stale_work():
    g = Generation()
    mine = g.value
    assert g.is_current(mine)
    g.bump()
    assert not g.is_current(mine)


def test_generation_threadsafe():
    g = Generation()
    ts = [threading.Thread(target=lambda: [g.bump() for _ in range(1000)]) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert g.value == 8000


def test_pct():
    assert pct([1, 2, 3, 4, 5], 0.5) == 3
    assert pct([10], 0.95) == 10


def test_timer_and_budget(tmp_path):
    log = tmp_path / "t.jsonl"
    for ms in (100, 200, 300):
        log.open("a").write(json.dumps({"ts": 0, "kind": "echo", "stt_final": ms, "tts_first_audio": ms * 3}) + "\n")
    text, ok = report({"echo": {"stt_final": 400, "tts_first_audio": 700}}, log)
    assert ok and "stt_final" in text and "[echo]" in text
    text, ok = report({"echo": {"tts_first_audio": 500}}, log)  # p50 = 600 > 500
    assert not ok and "OVER" in text
    t = TurnTimer("x", log)
    t.mark("a")
    t.mark("a")  # first mark wins
    row = t.save()
    assert "a" in row and row["kind"] == "x"


def test_config_loads():
    from capri.config import load
    cfg = load()
    assert cfg["voice"]["voice"] == "af_bella"
    assert cfg["hotkey"]["keys"] == ["alt"]


def test_sentence_split():
    import sys
    import types
    # voice.py imports sounddevice at module level; stub it so the pure helper is testable here.
    sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
    from capri.voice import split_sentences
    assert split_sentences("Got it. Opening Spotify now! Anything else?") == [
        "Got it.", "Opening Spotify now!", "Anything else?"]
    assert split_sentences("no punctuation") == ["no punctuation"]
    assert split_sentences("  ") == []


def test_turn_flow_with_fakes(tmp_path):
    """One echo turn end to end, with fake STT/voice: logs stt_final and tts_first_audio."""
    import sys
    import types
    import numpy as np
    for mod in ("sounddevice",):
        sys.modules.setdefault(mod, types.ModuleType(mod))
    from capri.config import load
    from capri.main import Capri
    from capri.timing import TurnTimer

    class FakeSTT:
        name = "fake"
        def transcribe(self, audio, sr):
            return "open spotify"

    said = []

    class FakeVoice:
        def say(self, text, started):
            said.append(text)
            started.set()

    app = Capri.__new__(Capri)
    app.cfg, app.gen, app.stt, app.voice, app.actions = load(), Generation(), FakeSTT(), FakeVoice(), None
    timer = TurnTimer("turn", tmp_path / "t.jsonl")
    app.handle(np.zeros(16000, dtype=np.float32), timer)
    assert said == ["Can't do that one yet. I heard: open spotify"]
    assert timer.kind == "echo"
    assert {"stt_final", "first_audio"} <= set(timer.marks)
    # too-short audio is ignored
    app.handle(np.zeros(100, dtype=np.float32), TurnTimer("turn", tmp_path / "t.jsonl"))
    assert len(said) == 1


def test_app_matching():
    from capri.apps import App, match
    apps = [App("Spotify", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"),
            App("Spotify Widget Helper", "x"), App("Google Chrome", "Chrome"),
            App("Discord", "com.squirrel.Discord.Discord"), App("Calculator", "Microsoft.WindowsCalculator!App"),
            App("Visual Studio Code", "Microsoft.VisualStudioCode")]
    assert match("spotify", apps).name == "Spotify"
    assert match("chrome", apps).name == "Google Chrome"
    assert match("google chrome", apps).name == "Google Chrome"
    assert match("discord", apps).name == "Discord"
    assert match("calc", apps).name == "Calculator"
    assert match("vs code", apps).name == "Visual Studio Code"
    assert match("discrod", apps).name == "Discord"  # fuzzy
    assert match("photoshop", apps) is None


def test_actions_with_fake_windows(monkeypatch):
    """Ack-first flow for instant commands, with winctl stubbed (it needs Windows)."""
    import sys
    import types
    calls = []
    fake = types.ModuleType("capri.winctl")
    for fn in ("media_play_pause", "media_next", "media_prev", "mute_toggle"):
        setattr(fake, fn, lambda fn=fn: calls.append(fn))
    fake.volume_up = lambda p=10: calls.append(("up", p))
    fake.volume_down = lambda p=10: calls.append(("down", p))
    fake.volume_set = lambda p: calls.append(("set", p))
    fake.find_window = lambda q: 42 if q == "discord" else None
    fake.focus = lambda h: calls.append(("focus", h))
    fake.close = lambda h: calls.append(("close", h))
    fake.foreground = lambda: 7
    fake.minimize = lambda h: calls.append(("min", h))
    fake.maximize = lambda h: calls.append(("max", h))
    fake.top_windows = lambda: [(9, "Daft Punk - One More Time", "Spotify.exe")]  # playing
    monkeypatch.setitem(sys.modules, "capri.winctl", fake)
    monkeypatch.setitem(sys.modules, "capri.sysctl", fake)
    import capri
    monkeypatch.setattr(capri, "sysctl", fake, raising=False)   # `from . import sysctl` reads the package attribute
    sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
    sys.modules.pop("capri.actions", None)
    from capri.actions import Actions
    from capri.router import parse
    from capri.timing import TurnTimer

    heard = []

    class V:
        def ack(self, text, started=None):
            heard.append(text); started and started.set()
        def say(self, text, started=None):
            heard.append(text); started and started.set()
        def stop(self):
            heard.append("<stop>")

    class Apps:
        def find(self, q):
            return None

    a = Actions(V(), Apps())
    t = lambda: TurnTimer("instant", log=__import__("pathlib").Path("/tmp/capri_test.jsonl"))
    assert a.execute(parse("turn it up a lot"), t()) and calls[-1] == ("up", 30)
    assert a.execute(parse("pause the music"), t()) and calls[-1] == "media_play_pause"  # was playing
    calls.clear()
    assert a.execute(parse("resume"), t()) and calls == []  # already playing: don't toggle it off
    assert a.execute(parse("close discord"), t()) and calls[-1] == ("close", 42)
    assert a.execute(parse("switch to the weather"), t()) is False  # not a window -> brain
    assert a.execute(parse("switch to coding mode"), t()) is False  # no memory wired -> brain handles it
    assert a.execute(parse("open photoshop"), t()) and heard[-1] == "I couldn't find photoshop."
    timer = t()
    a.execute(parse("skip"), timer)
    assert {"action_done", "first_audio"} <= set(timer.marks)


def test_mode_switch_by_voice(tmp_path):
    from capri.actions import Actions
    from capri.memory import Memory
    from capri.router import parse
    from capri.timing import TurnTimer

    heard = []

    class V:
        def ack(self, text, started=None): heard.append(text)
        def say(self, text, started=None): heard.append(text)

    mem = Memory(tmp_path / "mem", tmp_path / "w.jsonl")
    a = Actions(V(), None, memory=mem)
    assert a.execute(parse("switch to assignments mode"), TurnTimer("instant", log=tmp_path / "t.jsonl"))
    assert heard[-1] == "Switched to assignments mode." and mem.active_mode()[0] == "assignments"

import sys
import types

import numpy as np
import pytest

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

from capri.vad import FRAME, Segmenter, find_wake  # noqa: E402


@pytest.mark.parametrize("text,rest", [
    ("Hey Capri, open Spotify.", "open Spotify."),
    ("Capri?", ""),
    ("Dr. Mia Capri, what can you help me out with", "what can you help me out with"),
    ("Kapri turn it up", "turn it up"),
    ("caprie what's the weather", "what's the weather"),
    ("Capree, skip this song", "skip this song"),
    ("okay capri. pause", "pause"),
])
def test_wake_variants(text, rest):
    assert find_wake(text) == (True, rest)


@pytest.mark.parametrize("text", ["I love Capri Sun", "Open Spotify", "Captain America", "the cap is red", ""])
def test_not_wake(text):
    heard, _ = find_wake(text)
    assert heard is ("capri" in text.lower())   # "Capri Sun" contains the name; the rest don't


def test_segmenter_finds_two_utterances():
    seg = Segmenter(threshold=0.5, end_silence_s=0.7, min_speech_s=0.3)
    probs = [0.0] * 20 + [0.9] * 40 + [0.05] * 30 + [0.9] * 15 + [0.0] * 30
    outs = []
    for p in probs:
        u = seg.push(np.full(FRAME, p, dtype=np.float32), p)
        if u is not None:
            outs.append(len(u) // FRAME)
    assert len(outs) == 2
    assert outs[0] >= 40 and outs[1] >= 15


def test_segmenter_drops_blips():
    seg = Segmenter(min_speech_s=0.3)
    probs = [0.0] * 10 + [0.9] * 3 + [0.0] * 40      # ~0.1 s cough
    assert all(seg.push(np.zeros(FRAME, np.float32), p) is None for p in probs)


# ---------------------------------------------------------------- listener state machine
class FakeVoice:
    def __init__(self):
        self.speaking, self.quiet_since, self.acks = False, 0.0, []

    def ack(self, text, started=None):
        self.acks.append(text)


class FakeGen:
    def __init__(self):
        self.value = 0

    def bump(self):
        self.value += 1


@pytest.fixture
def listener(monkeypatch):
    import capri.listen as L

    class NoVAD:
        def __init__(self, *a, **k): pass
        def __call__(self, f): return 0.0
    monkeypatch.setattr(L, "SileroVAD", NoVAD)

    app = types.SimpleNamespace(voice=FakeVoice(), gen=FakeGen(), busy=False, hotkey_active=False,
                                overlay=None, submitted=[], heard=[""])
    app.transcribe = lambda audio: app.heard.pop(0)
    app.submit_text = lambda text, timer: app.submitted.append(text)
    app._show = lambda s: None
    pl = L.PassiveListener(app, {"listen": {"idle_timeout_s": 10}})
    return pl, app


def hear(pl, app, text):
    app.heard = [text]
    pl._utterance(np.zeros(16000, np.float32))


def test_asleep_ignores_normal_talk(listener):
    pl, app = listener
    hear(pl, app, "yeah I'll be there at six")
    assert not pl.awake and app.submitted == []


def test_name_plus_command_wakes_and_runs(listener):
    pl, app = listener
    hear(pl, app, "Hey Capri, open Spotify.")
    assert pl.awake and app.submitted == ["open Spotify."]


def test_name_alone_answers_yeah_then_listens(listener):
    pl, app = listener
    hear(pl, app, "Capri?")
    assert pl.awake and app.voice.acks == ["Yeah?"] and app.submitted == []
    hear(pl, app, "what's the weather tomorrow")      # no name needed now
    assert app.submitted == ["what's the weather tomorrow"]


def test_ignores_her_own_voice_but_name_interrupts(listener):
    pl, app = listener
    hear(pl, app, "Capri open chrome")
    app.voice.speaking = True
    hear(pl, app, "Sure, opening Chrome now.")        # echo of her own answer
    assert app.submitted == ["open chrome"] and app.gen.value == 0
    hear(pl, app, "Capri stop")
    assert app.gen.value == 1 and app.submitted[-1] == "stop"


def test_sleeps_after_quiet(listener, monkeypatch):
    import capri.listen as L
    pl, app = listener
    t = [1000.0]
    monkeypatch.setattr(L.time, "monotonic", lambda: t[0])
    hear(pl, app, "Capri, hi there friend")
    assert pl.awake
    t[0] += 5
    pl._check_idle()
    assert pl.awake
    app.busy = True                                   # she's working: clock doesn't run
    t[0] += 30
    pl._check_idle()
    assert pl.awake
    app.busy = False
    t[0] += 10.5
    pl._check_idle()
    assert not pl.awake

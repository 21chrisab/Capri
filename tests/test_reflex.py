"""Reflex tier: real phrases from Chris's logs (Capri v2 timings.jsonl, Nov 12; old Capri, Nov 8)."""
import pytest

from capri.classifier import to_intent
from capri.reflex import Reflex, clauses
from capri.router import Intent, parse


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def fresh(classify=None) -> Reflex:
    return Reflex(classify=classify, now=Clock())


# Each of these went to Gemini or screen control (2-12 s) instead of acting instantly.
REAL = [
    ("Yeah It just came. Yeah, so let's bring this up Can you... Go and open my spotify",
     [Intent("app_open", "spotify")]),
    ("you Can you open up my gmail", [Intent("open_site", "https://mail.google.com")]),
    ("I said open up my gmail", [Intent("open_site", "https://mail.google.com")]),
    ("Could you do it again? Could you open up my Gmail?", [Intent("open_site", "https://mail.google.com")]),
    ("Thank you. I mean, open up Claude and my Google Chrome.",
     [Intent("app_open", "claude"), Intent("app_open", "google chrome")]),
    ("My bad. Nah, you're all good. Go ahead and can you open up my, uh, quad as well",
     [Intent("app_open", "quad as well")]),
    ("Okay. And then can you also play any song or change the song to the table by Rex orange.",
     [Intent("spotify_search", "the table by rex orange")]),
    ("Yeah, you raise it. I like that word. Raise the volume.", [Intent("volume_up", 10)]),
    ("Can you play The Table by Rex Orange County?", [Intent("spotify_search", "the table by rex orange county")]),
]


@pytest.mark.parametrize("text,want", REAL)
def test_real_phrases_act_instantly(text, want):
    assert fresh().decide(text) == want


NEW = [
    ("open youtube", Intent("open_site", "https://www.youtube.com")),
    ("go to x dot com", Intent("open_site", "https://x.com")),
    ("pull up google calendar", Intent("open_site", "https://calendar.google.com")),
    ("google best ramen near campus", Intent("web_search", "best ramen near campus")),
    ("search up how to solder smd", Intent("web_search", "how to solder smd")),
    ("snap this window to the left", Intent("snap_left")),
    ("move it right", Intent("snap_right")),
    ("new tab", Intent("new_tab")),
    ("close this tab", Intent("close_tab")),
    ("next tab", Intent("next_tab")),
    ("take a screenshot", Intent("screenshot")),
    ("Type: Hello World, this is Capri.", Intent("type_text", "Hello World, this is Capri.")),
    ("play Mona Lisa by Dominic Fike", Intent("spotify_search", "mona lisa by dominic fike")),
    ("play some music", Intent("spotify_play")),
]


@pytest.mark.parametrize("text,want", NEW)
def test_new_commands(text, want):
    assert parse(text) == want


def test_follow_ups_after_a_volume_change():
    r = fresh()
    first = r.decide("turn the volume up")
    assert first == [Intent("volume_up", 10)]
    r.note(first[0])
    assert r.decide("The hundred") == [Intent("volume_set", 100)]
    assert r.decide("Oh my gosh, just do a hundred.") == [Intent("volume_set", 100)]
    assert r.decide("Yo, do it higher.") == [Intent("volume_up", 10)]
    assert r.decide("quieter") == [Intent("volume_down", 10)]


def test_follow_ups_expire():
    r = fresh()
    r.note(Intent("volume_up", 10))
    r.now.t += 120
    assert r.decide("The hundred") == []
    assert r.decide("higher") == []


def test_again_repeats_the_last_action():
    r = fresh()
    r.note(Intent("media_next"))
    assert r.decide("again") == [Intent("media_next")]
    assert r.decide("next one") == [Intent("media_next")]


def test_launch_cooldown():
    r = fresh()
    r.note(Intent("app_open", "chrome"))
    assert r.recently_launched("chrome")
    r.now.t += 9
    assert not r.recently_launched("chrome")


# Must reach the brain: questions, chat, trailing-off and compound tasks.
TO_BRAIN = [
    "Hey Capri, could you open up my...",
    "Hey Capri, how you doing?",
    "Talk to me. How do you feel",
    "Can you tell me what's on my tab right now?",
    "What's the weather like tomorrow?",
    "open chrome and search for cats",
    "Remind me to call mom at six.",
    "search my files for the lab report",
    "I watched it a hundred times",
    "",
]


@pytest.mark.parametrize("text", TO_BRAIN)
def test_falls_through_to_brain(text):
    assert fresh().decide(text) == []


def test_numbers_alone_need_a_recent_volume_change():
    assert fresh().decide("a hundred") == []


def test_guard_rails_cap_and_dedupe():
    r = fresh()
    text = "open notepad. open notepad. open discord. open spotify. open steam."
    out = r.decide(text)
    assert len(out) == 3
    assert out[0] == Intent("app_open", "notepad") and out.count(Intent("app_open", "notepad")) == 1


def test_clauses_split_commands_from_chatter():
    assert clauses("Yeah. So can you... go and open my spotify") == ["Yeah.", "So can you", "go and open my spotify"]
    assert clauses("open Claude and my Google Chrome") == ["open Claude", "open Google Chrome"]


def test_classifier_used_only_when_rules_miss():
    seen = []

    def clf(text):
        seen.append(text)
        return Intent("media_pause")

    r = fresh(clf)
    assert r.decide("pause") == [Intent("media_pause")] and seen == []   # rules answered, model never asked
    assert r.decide("hold up stop the tunes for a sec") == [Intent("media_pause")] and seen
    seen.clear()
    assert r.decide("why is the sky blue?") == [] and seen == []          # questions never go to it


# The local model can only choose: payloads come from the user's own words.
def test_classifier_choice_rules():
    words = "yo throw on some rex orange county".split()
    assert to_intent({"intent": "spotify_search", "start": 4, "end": 6, "confidence": 0.9}, words) == \
        Intent("spotify_search", "rex orange county")
    assert to_intent({"intent": "spotify_search", "start": 4, "end": 6, "confidence": 0.5}, words) is None
    assert to_intent({"intent": "spotify_search", "start": 4, "end": 99, "confidence": 0.9}, words) is None
    assert to_intent({"intent": "none", "start": -1, "end": -1, "confidence": 1.0}, words) is None
    assert to_intent({"intent": "type_text", "start": 0, "end": 1, "confidence": 1.0}, words) is None  # not allowed
    assert to_intent({"intent": "window_close", "start": -1, "end": -1, "confidence": 1.0}, words) is None
    assert to_intent({"intent": "volume_set", "start": 3, "end": 3, "confidence": 0.95},
                     "make it like seventy percent".split()) == Intent("volume_set", 70)
    assert to_intent({"intent": "open_site", "start": 2, "end": 2, "confidence": 0.9},
                     "pull up youtube rn".split()) == Intent("open_site", "https://www.youtube.com")


def test_open_then_a_different_command():
    from capri.reflex import Reflex
    got = [(i.name, i.arg) for i in Reflex().decide("open discord and turn the volume down")]
    assert got[0] == ("app_open", "discord")
    assert got[1][0] == "volume_down"
    got = [i.name for i in Reflex().decide("open spotify and skip this song")]
    assert got == ["app_open", "media_next"]

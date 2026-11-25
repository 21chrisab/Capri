import pytest

from capri.router import Intent, normalize, parse

CASES = [
    ("Stop.", Intent("stop")),
    ("switch to coding mode", Intent("set_mode", "coding")),
    ("Capri, switch to content studio mode.", Intent("set_mode", "content_studio")),
    ("enter homework mode", Intent("set_mode", "assignments")),
    ("go to general mode", Intent("set_mode", "general")),
    ("switch to coding", Intent("app_focus", "coding")),   # no "mode": stays a window switch
    ("never mind", Intent("stop")),
    ("Thanks, Capri!", Intent("thanks")),
    ("What time is it?", Intent("time")),
    ("Open Spotify.", Intent("app_open", "spotify")),
    ("Hey Capri, can you open Discord for me?", Intent("app_open", "discord")),
    ("launch google chrome", Intent("app_open", "google chrome")),
    ("pull up the calculator app", Intent("app_open", "calculator")),
    ("Close Discord.", Intent("app_close", "discord")),
    ("close this window", Intent("window_close")),
    ("Close it.", Intent("window_close")),
    ("minimize this", Intent("window_min")),
    ("Maximize the window.", Intent("window_max")),
    ("switch to chrome", Intent("app_focus", "chrome")),
    ("Skip.", Intent("media_next")),
    ("next song", Intent("media_next")),
    ("skip this track", Intent("media_next")),
    ("previous track", Intent("media_prev")),
    ("go back", Intent("media_prev")),
    ("Pause the music.", Intent("media_pause")),
    ("pause", Intent("media_pause")),
    ("resume", Intent("media_play")),
    ("Play some music.", Intent("spotify_play")),
    ("Open Spotify and play my liked songs.", Intent("spotify_play")),
    ("play", Intent("spotify_play")),
    ("Turn it up.", Intent("volume_up", 10)),
    ("turn the volume down a little", Intent("volume_down", 10)),
    ("turn it down a lot", Intent("volume_down", 30)),
    ("louder", Intent("volume_up", 10)),
    ("Set the volume to 40.", Intent("volume_set", 40)),
    ("volume 25%", Intent("volume_set", 25)),
    ("set volume to fifty", Intent("volume_set", 50)),
    ("volume to max", Intent("volume_set", 100)),
    ("Mute.", Intent("mute")),
    ("unmute", Intent("unmute")),
]

FALL_THROUGH = [
    "What's the weather like tomorrow?",
    "Open Spotify and then text my mom",
    "Remind me to call mom at six.",
    "Why is the sky blue?",
    "open chrome and search for cats",
    "",
]


@pytest.mark.parametrize("text,want", CASES)
def test_parse(text, want):
    assert parse(text) == want


@pytest.mark.parametrize("text", FALL_THROUGH)
def test_falls_through_to_brain(text):
    assert parse(text) is None


def test_normalize_strips_filler():
    assert normalize("Hey Capri, could you please open Spotify for me?") == "open spotify"

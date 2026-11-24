"""Tests for capri.macctl (mocked subprocess.run, runs on any OS)."""
import subprocess
import types

from capri import macctl


def _mock_osa(monkeypatch, responses):
    """Patch subprocess.run in macctl; responses is a callable(script) -> str or list[str]."""
    calls: list[str] = []

    def fake_run(cmd, capture_output=True, text=True, timeout=5.0):
        assert cmd[:2] == ["osascript", "-e"]
        script = cmd[2]
        calls.append(script)
        out = responses(script) if callable(responses) else responses.pop(0)
        return types.SimpleNamespace(stdout=out, stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_q_escapes_quotes_and_backslashes():
    assert macctl._q('Safari') == '"Safari"'
    assert macctl._q('a"b\\c') == '"a\\"b\\\\c"'
    assert macctl._q('foo\\"bar') == '"foo\\\\\\"bar"'


def test_volume_controls_and_clamping(monkeypatch):
    calls = _mock_osa(monkeypatch, lambda s: "95" if "get volume settings" in s else "")
    macctl.volume_up(10)
    assert calls[-1] == "set volume output volume 100"

    calls = _mock_osa(monkeypatch, lambda s: "5" if "get volume settings" in s else "")
    macctl.volume_down(20)
    assert calls[-1] == "set volume output volume 0"

    calls = _mock_osa(monkeypatch, lambda s: "")
    macctl.volume_set(150)
    assert calls[-1] == "set volume output volume 100"
    macctl.volume_set(-10)
    assert calls[-1] == "set volume output volume 0"
    macctl.mute_toggle()
    assert calls[-1] == "set volume output muted (not (output muted of (get volume settings)))"


def test_spotify_playing_states(monkeypatch):
    _mock_osa(monkeypatch, lambda s: "false" if "exists process" in s else "")
    assert macctl.spotify_playing() is None

    _mock_osa(monkeypatch, lambda s: "true" if "exists process" in s else "playing")
    assert macctl.spotify_playing() is True

    _mock_osa(monkeypatch, lambda s: "true" if "exists process" in s else "paused")
    assert macctl.spotify_playing() is False


def test_media_keys_only_when_spotify_running(monkeypatch):
    calls = _mock_osa(monkeypatch, lambda s: "false")
    macctl.media_play_pause()
    assert len(calls) == 1  # only checked is_running

    calls = _mock_osa(monkeypatch, lambda s: "true" if "exists process" in s else "")
    macctl.media_play_pause()
    macctl.media_next()
    macctl.media_prev()
    assert 'tell application "Spotify" to playpause' in calls
    assert 'tell application "Spotify" to next track' in calls
    assert 'tell application "Spotify" to previous track' in calls


def test_find_window_fuzzy_match(monkeypatch):
    procs = "Finder\tDesktop\nGoogle Chrome\tNew Tab\nVisual Studio Code\tmain.py\nSpotify\tDaft Punk\n"
    _mock_osa(monkeypatch, lambda s: procs)
    assert macctl.find_window("chrome") == "Google Chrome"
    assert macctl.find_window("vs code") == "Visual Studio Code"
    assert macctl.find_window("spotfy") == "Spotify"
    assert macctl.find_window("photoshop") is None


def test_focus_close_minimize_maximize_and_monitors(monkeypatch):
    calls = _mock_osa(monkeypatch, lambda s: "0, 0, 1440, 900" if "bounds" in s else "Finder")
    macctl.focus('App "1"')
    assert calls[-1] == 'tell application "System Events" to set frontmost of process "App \\"1\\"" to true'
    assert macctl.foreground() == "Finder"
    macctl.close("Safari")
    assert "AXCloseButton" in calls[-1] and 'keystroke "w" using command down' in calls[-1]
    macctl.minimize("Safari")
    assert 'AXMinimized' in calls[-1]
    macctl.maximize("Safari")
    assert "bounds of window of desktop" in calls[-1]
    assert macctl.monitors() == [(0, 0, 1440, 900)]

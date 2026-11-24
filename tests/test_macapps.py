"""Tests for capri.macapps (scan, aliases, launch)."""
import subprocess
import types

from capri import macapps
from capri.apps import App


def test_scan_lists_top_level_app_bundles(tmp_path):
    apps_dir = tmp_path / "Applications"
    utils_dir = apps_dir / "Utilities"
    apps_dir.mkdir()
    utils_dir.mkdir()
    (apps_dir / "Safari.app").mkdir()
    (apps_dir / "Spotify.app").mkdir()
    (apps_dir / "Spotify.app" / "Contents").mkdir()
    (apps_dir / "Spotify.app" / "Contents" / "Nested.app").mkdir()  # must not recurse
    (apps_dir / "readme.txt").write_text("hi")
    (utils_dir / "Terminal.app").mkdir()

    cache = tmp_path / "logs" / "apps.json"
    found = macapps.scan(dirs=[apps_dir, utils_dir, tmp_path / "Missing"], cache=cache)
    names = [a.name for a in found]
    assert names == ["Safari", "Spotify", "Terminal"]
    assert found[0].app_id == str(apps_dir / "Safari.app")
    assert cache.exists()


def test_mac_aliases_and_fuzzy_match():
    apps = [
        App("Google Chrome", "/Applications/Google Chrome.app"),
        App("Safari", "/System/Applications/Safari.app"),
        App("Visual Studio Code", "/Applications/Visual Studio Code.app"),
        App("Terminal", "/System/Applications/Utilities/Terminal.app"),
        App("System Settings", "/System/Applications/System Settings.app"),
        App("Finder", "/System/Library/CoreServices/Finder.app"),
        App("Calculator", "/System/Applications/Calculator.app"),
        App("Notes", "/System/Applications/Notes.app"),
        App("Music", "/System/Applications/Music.app"),
    ]
    assert macapps.match("chrome", apps).name == "Google Chrome"
    assert macapps.match("browser", apps).name == "Safari"
    assert macapps.match("vs code", apps).name == "Visual Studio Code"
    assert macapps.match("vscode", apps).name == "Visual Studio Code"
    assert macapps.match("code", apps).name == "Visual Studio Code"
    assert macapps.match("terminal", apps).name == "Terminal"
    assert macapps.match("settings", apps).name == "System Settings"
    assert macapps.match("files", apps).name == "Finder"
    assert macapps.match("finder", apps).name == "Finder"
    assert macapps.match("calc", apps).name == "Calculator"
    assert macapps.match("notes", apps).name == "Notes"
    assert macapps.match("music", apps).name == "Music"
    assert macapps.match("photoshop", apps) is None


def test_launch_uses_open_minus_a(monkeypatch):
    launched = []

    def fake_popen(cmd, **kwargs):
        launched.append(cmd)
        return types.SimpleNamespace(pid=123)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    macapps.launch(App("Spotify", "/Applications/Spotify.app"))
    assert launched == [["open", "-a", "/Applications/Spotify.app"]]

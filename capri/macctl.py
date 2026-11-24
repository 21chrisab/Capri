"""Small macOS controls via osascript (AppleScript): Spotify media, volume, window find/focus/close.

Window "handles" on macOS are app names (str).
"""
from __future__ import annotations

import subprocess
import time

from . import macapps
from .apps import App


def _q(s: str) -> str:
    """Return a safely quoted AppleScript string literal (one line; quotes and backslashes escaped)."""
    s = " ".join(str(s).split())
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _osa(script: str, timeout: float = 5.0) -> str:
    """Run an AppleScript snippet via osascript and return stripped stdout."""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""                      # a hung or missing osascript must never crash a turn
    return (r.stdout or "").strip()


# --- media / volume ---
def _spotify_cmd(cmd: str) -> None:
    if is_running("Spotify"):
        _osa(f'tell application "Spotify" to {cmd}')


def media_play_pause() -> None:
    _spotify_cmd("playpause")


def media_next() -> None:
    _spotify_cmd("next track")


def media_prev() -> None:
    _spotify_cmd("previous track")


def _get_volume() -> int:
    out = _osa("output volume of (get volume settings)")
    try:
        return int(out)
    except ValueError:
        return 50


def volume_set(pct: int) -> None:
    n = max(0, min(100, int(pct)))
    _osa(f"set volume output volume {n}")


def volume_up(pct: int = 10) -> None:
    volume_set(_get_volume() + pct)


def volume_down(pct: int = 10) -> None:
    volume_set(_get_volume() - pct)


def mute_toggle() -> None:
    _osa("set volume output muted (not (output muted of (get volume settings)))")


def spotify_playing() -> bool | None:
    """None if Spotify isn't running, True if playing, False if paused/stopped."""
    if not is_running("Spotify"):
        return None
    state = _osa('tell application "Spotify" to player state as string').lower()
    if state == "playing":
        return True
    if state in ("paused", "stopped"):
        return False
    return None


# --- windows ---
def top_windows() -> list[tuple[str, str, str]]:
    """Visible app processes: (app_name, front_window_title_or_app_name, app_name)."""
    script = (
        'tell application "System Events"\n'
        '    set out to ""\n'
        '    repeat with p in (every process whose background only is false)\n'
        '        set pname to name of p\n'
        '        set wtitle to pname\n'
        '        try\n'
        '            if (count of windows of p) > 0 then\n'
        '                set t to name of front window of p\n'
        '                if t is not missing value and t is not "" then set wtitle to t\n'
        '            end if\n'
        '        end try\n'
        '        set out to out & pname & tab & wtitle & linefeed\n'
        '    end repeat\n'
        '    return out\n'
        'end tell'
    )
    out = _osa(script)
    rows: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            pname, wtitle = line.split("\t", 1)
        else:
            pname, wtitle = line, line
        pname = pname.strip()
        wtitle = wtitle.strip() or pname
        if pname:
            rows.append((pname, wtitle, pname))
    return rows


def find_window(query: str) -> str | None:
    """Fuzzy-match query against running visible app names."""
    seen: set[str] = set()
    apps: list[App] = []
    for handle, _, proc in top_windows():
        if proc not in seen:
            seen.add(proc)
            apps.append(App(name=proc, app_id=handle))
    hit = macapps.match(query, apps)
    return hit.app_id if hit is not None else None


def monitors() -> list[tuple[int, int, int, int]]:
    """Main screen frame (left, top, right, bottom)."""
    out = _osa('tell application "Finder" to get bounds of window of desktop')
    parts = [p.strip() for p in out.split(",") if p.strip()]
    if len(parts) == 4:
        try:
            return [(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))]
        except ValueError:
            pass
    return [(0, 0, 1920, 1080)]


def is_running(name: str) -> bool:
    return _osa(f'tell application "System Events" to exists process {_q(name)}').lower() == "true"


def focus(h: str) -> None:
    if not h:
        return
    # By process name: process names can differ from bundle names ("Code" vs "Visual Studio Code").
    _osa(f'tell application "System Events" to set frontmost of process {_q(h)} to true')


def foreground() -> str:
    return _osa('tell application "System Events" to get name of first process whose frontmost is true')


def close(h: str) -> None:
    """Close the front window of app h without quitting the app."""
    if not h:
        return
    q = _q(h)
    _osa(
        f'tell application "System Events"\n'
        f'    set frontmost of process {q} to true\n'
        f'end tell\n'
        f'tell application "System Events"\n'
        f'    tell process {q}\n'
        f'        if (count of windows) > 0 then\n'
        f'            try\n'
        f'                click (first button of front window whose subrole is "AXCloseButton")\n'
        f'            on error\n'
        f'                keystroke "w" using command down\n'
        f'            end try\n'
        f'        end if\n'
        f'    end tell\n'
        f'end tell'
    )


def minimize(h: str) -> None:
    if not h:
        return
    q = _q(h)
    _osa(
        f'tell application "System Events"\n'
        f'    tell process {q}\n'
        f'        if (count of windows) > 0 then\n'
        f'            set value of attribute "AXMinimized" of front window to true\n'
        f'        end if\n'
        f'    end tell\n'
        f'end tell'
    )


def maximize(h: str) -> None:
    if not h:
        return
    q = _q(h)
    _osa(
        f'tell application "Finder" to set b to bounds of window of desktop\n'
        f'set x1 to item 1 of b\n'
        f'set y1 to item 2 of b\n'
        f'set x2 to item 3 of b\n'
        f'set y2 to item 4 of b\n'
        f'tell application "System Events"\n'
        f'    tell process {q}\n'
        f'        if (count of windows) > 0 then\n'
        f'            set position of front window to {{x1, y1 + 25}}\n'
        f'            set size of front window to {{x2 - x1, y2 - y1 - 25}}\n'
        f'        end if\n'
        f'    end tell\n'
        f'end tell'
    )


def wait_for_window(query: str, timeout: float = 8.0) -> str | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        h = find_window(query)
        if h:
            return h
        time.sleep(0.5)                # each check is a System Events round trip
    return None

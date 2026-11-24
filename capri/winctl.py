"""Small Windows controls via ctypes: media/volume keys, window find/focus/close.

No UI Automation yet (that's M4). Everything here is instant and needs no AI.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

try:
    import psutil
except Exception:  # ImportError, or psutil refusing an unsupported (test-patched) platform
    psutil = None  # type: ignore[assignment]

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None  # type: ignore[attr-defined]

VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB3
VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x2
WM_CLOSE = 0x0010
SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9


def tap(vk: int, times: int = 1) -> None:
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


# --- media / volume (each volume key step is 2%) ---
def media_play_pause() -> None: tap(VK_MEDIA_PLAY_PAUSE)
def media_next() -> None: tap(VK_MEDIA_NEXT)
def media_prev() -> None: tap(VK_MEDIA_PREV)
def volume_up(pct: int = 10) -> None: tap(VK_VOLUME_UP, max(1, pct // 2))
def volume_down(pct: int = 10) -> None: tap(VK_VOLUME_DOWN, max(1, pct // 2))
def mute_toggle() -> None: tap(VK_VOLUME_MUTE)


VK_LWIN, VK_CONTROL, VK_TAB, VK_LEFT, VK_RIGHT, VK_SNAPSHOT = 0x5B, 0x11, 0x09, 0x25, 0x27, 0x2C


def combo(*vks: int) -> None:
    """Press a key chord: all down in order, all up in reverse."""
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def snap(side: str) -> None: combo(VK_LWIN, VK_LEFT if side == "left" else VK_RIGHT)
def new_tab() -> None: combo(VK_CONTROL, ord("T"))
def close_tab() -> None: combo(VK_CONTROL, ord("W"))
def next_tab() -> None: combo(VK_CONTROL, VK_TAB)
def screenshot() -> None: combo(VK_LWIN, VK_SNAPSHOT)   # Windows saves it to Pictures\Screenshots


def volume_set(pct: int) -> None:
    tap(VK_VOLUME_DOWN, 50)          # floor it, then step up; no COM dependency
    tap(VK_VOLUME_UP, round(pct / 2))


# --- windows ---
def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _pid(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def top_windows() -> list[tuple[int, str, str]]:
    """Visible top-level windows with a title: (hwnd, title, process name)."""
    out: list[tuple[int, str, str]] = []
    proc_names: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = _pid(hwnd)
            if pid not in proc_names:
                try:
                    proc_names[pid] = psutil.Process(pid).name()
                except psutil.Error:
                    proc_names[pid] = ""
            out.append((hwnd, _title(hwnd), proc_names[pid]))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(query: str) -> int | None:
    q = query.lower().replace(" ", "")
    best = None
    for hwnd, title, proc in top_windows():
        p = proc.lower().removesuffix(".exe")
        if q == p or q in p:
            return hwnd
        if best is None and q in title.lower().replace(" ", ""):
            best = hwnd
    return best


def window_pid(hwnd: int) -> int:
    return _pid(hwnd)


def monitors() -> list[tuple[int, int, int, int]]:
    """Monitor rectangles (left, top, right, bottom) in virtual-screen pixels, primary first."""
    rects: list[tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
    def cb(hmon, hdc, lprc, _):
        r = lprc.contents
        rects.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumDisplayMonitors(0, 0, cb, 0)
    rects.sort(key=lambda r: (r[0] != 0 or r[1] != 0, r[0], r[1]))  # primary sits at (0, 0)
    return rects


def key_down(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)


def key_up(vk: int) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def is_running(process_name: str) -> bool:
    n = process_name.lower()
    return any((p.info.get("name") or "").lower() == n for p in psutil.process_iter(["name"]))


def focus(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    tap(VK_MENU)  # Windows only lets the foreground app change focus; a synthetic Alt satisfies that
    user32.SetForegroundWindow(hwnd)


def foreground() -> int:
    return user32.GetForegroundWindow()


def close(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)   # polite close: apps can still ask "save changes?"


def minimize(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_MINIMIZE)


def maximize(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def wait_for_window(query: str, timeout: float = 8.0) -> int | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        hwnd = find_window(query)
        if hwnd:
            return hwnd
        time.sleep(0.15)
    return None

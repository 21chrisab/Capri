"""Alt-only hold-to-talk logic, with fake key states and fake time (no Windows needed)."""
import ctypes
import types

import pytest


@pytest.fixture
def make(monkeypatch):
    down: set[int] = set()
    taps: list[int] = []

    class U32:
        def GetAsyncKeyState(self, k):
            return 0x8000 if k in down else 0

        def keybd_event(self, vk, scan, flags, extra):
            if flags == 0:
                taps.append(vk)

    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=U32()), raising=False)
    from capri.hotkey import HoldHotkey

    def build(keys):
        ev = []
        hk = HoldHotkey(keys, lambda: ev.append("press"), lambda: ev.append("release"),
                        on_esc=lambda: ev.append("esc"), on_confirm=lambda: ev.append("confirm"),
                        on_cancel=lambda: ev.append("cancel"))
        return hk, ev
    return build, down, taps


ALT, TAB, F4, SPACE, CTRL, LBUTTON = 0x12, 0x09, 0x73, 0x20, 0x11, 0x01


def drive(hk, down, script):
    """script: list of (time, set_of_keys_down)."""
    for t, keys in script:
        down.clear()
        down.update(keys)
        hk.step(t)


def test_clean_alt_hold_talks(make):
    build, down, taps = make
    hk, ev = build(["alt"])
    drive(hk, down, [(0.0, {ALT}), (0.1, {ALT}), (0.25, {ALT}), (1.5, {ALT}), (1.6, set())])
    assert ev == ["press", "confirm", "release"]
    assert taps == [0xE8]          # menu-mask tap so releasing Alt doesn't open the menu bar


def test_alt_tab_is_ignored(make):
    build, down, taps = make
    hk, ev = build(["alt"])
    drive(hk, down, [(0.0, {ALT}), (0.05, {ALT, TAB}), (0.3, {ALT}), (0.6, set())])
    assert ev == ["press", "cancel"]     # no confirm, no release: Capri never reacts
    assert taps == []


def test_alt_f4_after_confirm_cancels(make):
    build, down, _ = make
    hk, ev = build(["alt"])
    drive(hk, down, [(0.0, {ALT}), (0.3, {ALT}), (0.5, {ALT, F4}), (0.7, set())])
    assert ev == ["press", "confirm", "cancel"]


def test_quick_tap_ignored(make):
    build, down, _ = make
    hk, ev = build(["alt"])
    drive(hk, down, [(0.0, {ALT}), (0.1, set())])
    assert ev == ["press", "cancel"]


def test_alt_click_ignored(make):
    build, down, _ = make
    hk, ev = build(["alt"])
    drive(hk, down, [(0.0, {ALT}), (0.1, {ALT, LBUTTON}), (0.5, set())])
    assert ev == ["press", "cancel"]


def test_alt_pressed_during_other_shortcut_is_ignored(make):
    build, down, _ = make
    hk, ev = build(["alt"])
    drive(hk, down, [(0.0, {CTRL}), (0.1, {CTRL, ALT}), (0.5, {CTRL, ALT}), (0.9, set())])
    assert ev == []                       # Ctrl+Alt = a shortcut, not Capri


def test_combo_hotkey_still_works(make):
    build, down, taps = make
    hk, ev = build(["ctrl", "alt", "space"])
    drive(hk, down, [(0.0, {CTRL, ALT, SPACE}), (1.0, {CTRL, ALT, SPACE}), (1.2, {CTRL})])
    assert ev == ["press", "confirm", "release"] and taps == []


def test_right_alt_only(make):
    build, down, _ = make
    hk, ev = build(["ralt"])
    drive(hk, down, [(0.0, {0xA4, ALT}), (0.5, set())])          # left Alt: nothing
    drive(hk, down, [(1.0, {0xA5, ALT}), (1.3, {0xA5, ALT}), (1.6, set())])
    assert ev == ["press", "confirm", "release"]

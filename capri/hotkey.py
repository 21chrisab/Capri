"""Hold-to-talk hotkey via GetAsyncKeyState polling (no keyboard hook, no extra package).

Two kinds of hotkey:
- A combo with a normal key (e.g. ctrl+alt+space): press = start, release = stop.
- Modifier only (e.g. just "alt"): has to be a *clean* hold so normal shortcuts still work.
    * Recording starts the instant Alt goes down (no lost first word) but stays tentative.
    * If any other key or mouse button is pressed while Alt is held (Alt+Tab, Alt+F4,
      Alt+click...), the turn is cancelled silently and nothing happens.
    * After 200 ms of clean hold it's confirmed: Capri stops talking and shows "Listening".
      A harmless unassigned key (0xE8) is tapped once so releasing Alt doesn't pop open the
      app's menu bar (the same trick AutoHotkey uses).
    * Quick taps (< 0.25 s) are ignored.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable

VK = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "space": 0x20, "esc": 0x1B,
    "lalt": 0xA4, "ralt": 0xA5, "lctrl": 0xA2, "rctrl": 0xA3, "lshift": 0xA0, "rshift": 0xA1,
    "capslock": 0x14, "f13": 0x7C, "f14": 0x7D,
}
MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
# Generic modifier -> its left/right variants (GetAsyncKeyState reports both).
FAMILY = {0x10: {0xA0, 0xA1}, 0x11: {0xA2, 0xA3}, 0x12: {0xA4, 0xA5}, 0x5B: {0x5C},
          0xA0: {0x10}, 0xA1: {0x10}, 0xA2: {0x11}, 0xA3: {0x11}, 0xA4: {0x12}, 0xA5: {0x12}}
MASK_KEY = 0xE8          # unassigned virtual key: stops Alt-release from activating menus
CONFIRM_S = 0.20
MIN_HOLD_S = 0.25
# Keys that break a modifier-only hold: mouse buttons 1-6 + every keyboard key except modifiers/mask.
OTHER_KEYS = [k for k in range(0x01, 0xFF) if k not in MODIFIERS and k not in (MASK_KEY, 0x07, 0x0A, 0x0B)]


def vk_code(name: str) -> int:
    name = name.lower()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ValueError(f"Unknown key name: {name}")


_MAC_PERM_MSG = (
    "Capri can't see the keyboard yet: System Settings → Privacy & Security → "
    "Input Monitoring (and Accessibility) → enable your Terminal app, then restart Capri."
)


class Win32Keys:
    """Windows key-state backend via GetAsyncKeyState + keybd_event."""

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    def down(self, code: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(code) & 0x8000)

    def other_key_down(self, own: set[int], other_keys: list[int]) -> bool:
        return any(self.down(k) for k in other_keys)

    def tap_mask(self) -> None:
        self._user32.keybd_event(MASK_KEY, 0, 0, 0)
        self._user32.keybd_event(MASK_KEY, 0, 2, 0)


class PynputKeys:
    """macOS key-state backend via pynput keyboard + mouse listeners."""

    def __init__(self) -> None:
        self._pressed: set[int] = set()
        self._extra: dict[object, int] = {}
        self._lock = threading.Lock()
        self._warned = False
        self._kb = None
        self._mouse = None

    def _warn_once(self) -> None:
        if not self._warned:
            self._warned = True
            print(_MAC_PERM_MSG, flush=True)

    @staticmethod
    def _key_map(keyboard_mod) -> dict[object, int]:
        K = keyboard_mod.Key
        mapping: dict[object, int] = {}
        for attr, code in (
            ("alt_l", 0xA4), ("alt_r", 0xA5), ("alt", 0x12), ("alt_gr", 0xA5),
            ("cmd", 0x5B), ("cmd_l", 0x5B), ("cmd_r", 0x5B),
            ("ctrl", 0x11), ("ctrl_l", 0xA2), ("ctrl_r", 0xA3),
            ("shift", 0x10), ("shift_l", 0xA0), ("shift_r", 0xA1),
            ("space", 0x20), ("esc", 0x1B), ("f13", 0x7C), ("f14", 0x7D), ("caps_lock", 0x14),
        ):
            if hasattr(K, attr):
                mapping[getattr(K, attr)] = code
        return mapping

    def _code_for_key(self, key, key_map: dict[object, int]) -> int:
        if key in key_map:
            return key_map[key]
        ch = getattr(key, "char", None)
        if isinstance(ch, str) and len(ch) == 1 and ch.isalnum():
            return ord(ch.upper())
        return self._extra.setdefault(key, 0x100 + len(self._extra))

    def _code_for_button(self, button, mouse_mod) -> int:
        B = mouse_mod.Button
        if button == B.left:
            return 0x01
        if button == B.right:
            return 0x02
        if button == B.middle:
            return 0x04
        return self._extra.setdefault(button, 0x100 + len(self._extra))

    def start(self) -> None:
        if self._kb is not None:
            return
        try:
            from pynput import keyboard, mouse
            key_map = self._key_map(keyboard)

            def on_press(k) -> None:
                with self._lock:
                    self._pressed.add(self._code_for_key(k, key_map))

            def on_release(k) -> None:
                with self._lock:
                    self._pressed.discard(self._code_for_key(k, key_map))

            def on_click(_x, _y, button, pressed: bool) -> None:
                with self._lock:
                    c = self._code_for_button(button, mouse)
                    if pressed:
                        self._pressed.add(c)
                    else:
                        self._pressed.discard(c)

            self._kb = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._mouse = mouse.Listener(on_click=on_click)
            self._kb.start()
            self._mouse.start()
            if not getattr(self._kb, "IS_TRUSTED", True):
                self._warn_once()
        except Exception:
            self._warn_once()

    def stop(self) -> None:
        for listener in (self._kb, self._mouse):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass

    def down(self, code: int) -> bool:
        family = {code} | FAMILY.get(code, set())
        with self._lock:
            return bool(self._pressed & family)

    def other_key_down(self, own: set[int], other_keys: list[int]) -> bool:
        with self._lock:
            return bool(self._pressed - own)

    def tap_mask(self) -> None:
        pass


class HoldHotkey:
    def __init__(self, keys: list[str], on_press: Callable[[], None], on_release: Callable[[], None],
                 on_esc: Callable[[], None] | None = None, on_confirm: Callable[[], None] | None = None,
                 on_cancel: Callable[[], None] | None = None, hz: int = 100, keys_backend=None) -> None:
        self.codes = [vk_code(k) for k in keys]
        self.modifier_only = all(c in MODIFIERS for c in self.codes)
        own = set(self.codes) | {v for c in self.codes for v in FAMILY.get(c, ())}
        if 0x12 in own:            # holding "alt": left/right Alt are both "ours"
            own |= {0xA4, 0xA5}
        self.own = own
        # Anything else held (incl. Ctrl/Shift/Win) means it's a shortcut, not Capri.
        self.other_keys = OTHER_KEYS + sorted(MODIFIERS - own)
        self.on_press, self.on_release, self.on_esc = on_press, on_release, on_esc
        self.on_confirm = on_confirm or (lambda: None)
        self.on_cancel = on_cancel or (lambda: None)
        self.period = 1 / hz
        self._stop = threading.Event()
        if keys_backend is not None:
            self.keys = keys_backend
        elif sys.platform == "darwin" and not hasattr(ctypes, "windll"):
            self.keys = PynputKeys()
        else:
            self.keys = Win32Keys()

    def _down(self, code: int) -> bool:
        return self.keys.down(code)

    def _other_key_down(self) -> bool:
        return self.keys.other_key_down(self.own, self.other_keys)

    def _tap_mask(self) -> None:
        self.keys.tap_mask()

    def run(self) -> None:
        while not self._stop.is_set():
            self.step(time.monotonic())
            time.sleep(self.period)

    def step(self, t: float) -> None:
        """One poll. Separated from run() so the logic is testable with fake time and keys."""
        st = self.__dict__.setdefault("_st", {"held": False, "esc": False, "confirmed": False,
                                               "broken": False, "t_down": 0.0})
        now = all(self._down(c) for c in self.codes)
        if now and not st["held"]:
            # Modifier-only: ignore if another key was already down (user is mid-shortcut).
            st["broken"] = self.modifier_only and self._other_key_down()
            st["confirmed"] = not self.modifier_only
            st["t_down"] = t
            if not st["broken"]:
                self.on_press()
                if st["confirmed"]:
                    self.on_confirm()
        elif now and st["held"] and self.modifier_only and not st["broken"]:
            if self._other_key_down():
                st["broken"] = True
                self.on_cancel()              # Alt+Tab / Alt+F4 / Alt+click: not for Capri
            elif not st["confirmed"] and t - st["t_down"] >= CONFIRM_S:
                st["confirmed"] = True
                self._tap_mask()
                self.on_confirm()
        elif st["held"] and not now and not st["broken"]:
            if t - st["t_down"] < MIN_HOLD_S:
                self.on_cancel()              # quick tap
            else:
                self.on_release()
        st["held"] = now
        esc = self._down(VK["esc"])
        if esc and not st["esc"] and self.on_esc:
            self.on_esc()
        st["esc"] = esc

    def start(self) -> threading.Thread:
        if hasattr(self.keys, "start"):
            self.keys.start()
        t = threading.Thread(target=self.run, name="hotkey", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
        if hasattr(self.keys, "stop"):
            self.keys.stop()

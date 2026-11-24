"""macOS Option hold-to-talk hotkey tests using a PynputKeys-style fake backend."""
from capri.hotkey import FAMILY, HoldHotkey

LALT, RALT, TAB, ESC = 0xA4, 0xA5, 0x09, 0x1B


class FakeMacKeys:
    def __init__(self) -> None:
        self.pressed: set[int] = set()
        self.masked = 0

    def down(self, code: int) -> bool:
        family = {code} | FAMILY.get(code, set())
        return bool(self.pressed & family)

    def other_key_down(self, own: set[int], _other_keys: list[int]) -> bool:
        return bool(self.pressed - own)

    def tap_mask(self) -> None:
        self.masked += 1


def _build(keys=("alt",)):
    backend = FakeMacKeys()
    ev: list[str] = []
    hk = HoldHotkey(
        list(keys),
        lambda: ev.append("press"),
        lambda: ev.append("release"),
        on_esc=lambda: ev.append("esc"),
        on_confirm=lambda: ev.append("confirm"),
        on_cancel=lambda: ev.append("cancel"),
        keys_backend=backend,
    )
    return hk, backend, ev


def _drive(hk, backend, script):
    for t, keys in script:
        backend.pressed = set(keys)
        hk.step(t)


def test_option_hold_press_confirm_release():
    hk, backend, ev = _build()
    _drive(hk, backend, [(0.0, {LALT}), (0.1, {LALT}), (0.25, {LALT}), (0.5, set())])
    assert ev == ["press", "confirm", "release"]
    assert backend.masked == 1  # tap_mask called on confirm (no-op in real PynputKeys)


def test_option_tab_cancels():
    hk, backend, ev = _build()
    _drive(hk, backend, [(0.0, {LALT}), (0.05, {LALT, TAB}), (0.3, {LALT}), (0.6, set())])
    assert ev == ["press", "cancel"]


def test_left_and_right_option_both_count():
    hk, backend, ev = _build()
    _drive(hk, backend, [(0.0, {LALT}), (0.25, {LALT}), (0.5, set()),
                         (1.0, {RALT}), (1.25, {RALT}), (1.5, set())])
    assert ev == ["press", "confirm", "release", "press", "confirm", "release"]


def test_esc_callback():
    hk, backend, ev = _build()
    _drive(hk, backend, [(0.0, {ESC}), (0.05, {ESC}), (0.1, set())])
    assert ev == ["esc"]

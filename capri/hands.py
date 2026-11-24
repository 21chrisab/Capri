"""Capri's eyes and hands for any app, on either monitor.

How she "sees", cheapest first:
  1. UI Automation (accessibility tree). Windows already knows every button,
     text box and list item in most apps, with its name and exact rectangle.
     observe() turns that into a short numbered text list. The model answers
     with an id and Windows supplies the coordinates. No image, no guessing.
  2. Windows OCR (built into Windows, local, fast). For apps whose tree is empty
     (games, some Electron/canvas apps), observe() falls back to on-screen words
     with ids. read_screen() returns a window's text for reading.
  3. Vision (look). A downscaled screenshot of ONE window goes to Gemini Flash
     with a question. Last resort only.

After acting she waits 450 ms and compares a 16x16 hash of the window, so she
knows whether anything actually happened (closed loop, from NŪS).

Windows-only parts are imported lazily, so the pure logic is testable anywhere.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field

INTERACTIVE = {
    "ButtonControl", "SplitButtonControl", "EditControl", "ComboBoxControl", "ListItemControl",
    "MenuItemControl", "TabItemControl", "HyperlinkControl", "CheckBoxControl", "RadioButtonControl",
    "TreeItemControl", "DataItemControl", "SliderControl", "SpinnerControl",
}
READABLE = {"TextControl", "DocumentControl", "HeaderItemControl"}
SHORT = {k: k.removesuffix("Control") for k in INTERACTIVE | READABLE}

SENSITIVE = re.compile(r"pass(word|code|phrase)?|\bpin\b|cvv|cvc|security code|\bssn\b|social security|card number|"
                       r"credit card|routing number|account number|2fa|one.?time code|otp", re.I)
NEEDS_CONFIRM = re.compile(r"\b(send|delete|remove|discard|buy|purchase|pay|checkout|check out|place order|"
                           r"subscribe|uninstall|format|empty trash|empty recycle|sign out|log out|transfer|post|publish)\b",
                           re.I)

MAX_ITEMS = 90
MAX_TEXT_ITEMS = 25
WALK_BUDGET_S = 1.2
MIN_USEFUL = 6       # fewer named controls than this -> OCR fallback


# ---------------------------------------------------------------- pure logic
@dataclass
class Element:
    id: str
    kind: str                  # "uia" or "ocr"
    type: str                  # Button, Edit, ... or "Text" for OCR
    name: str
    rect: tuple[int, int, int, int]   # left, top, right, bottom (virtual-screen pixels)
    value: str = ""
    focused: bool = False
    ref: object = field(default=None, repr=False)   # the uiautomation Control (worker thread only)

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.rect
        return (l + r) // 2, (t + b) // 2


def pick_elements(raw: list[dict]) -> list[Element]:
    """Filter + rank raw UIA items into what the model needs to see.

    raw items: {name, type, rect, enabled, offscreen, focused, value, ref}
    Keeps visible, enabled, sized items. Interactive ones first (in screen
    order), then a few readable text items. Drops duplicates and unnamed
    non-edit controls.
    """
    inter, texts, seen = [], [], set()
    for it in raw:
        typ, name = it.get("type", ""), " ".join(str(it.get("name") or "").split())
        l, t, r, b = it.get("rect", (0, 0, 0, 0))
        if it.get("offscreen") or not it.get("enabled", True) or r - l < 4 or b - t < 4:
            continue
        if typ not in INTERACTIVE and typ not in READABLE:
            continue
        if not name and typ not in ("EditControl", "ComboBoxControl"):
            continue
        key = (typ, name, l // 8, t // 8)
        if key in seen:
            continue
        seen.add(key)
        (inter if typ in INTERACTIVE else texts).append(it)
    inter.sort(key=lambda it: (it["rect"][1] // 20, it["rect"][0]))
    out: list[Element] = []
    for it in inter[:MAX_ITEMS] + texts[:MAX_TEXT_ITEMS]:
        out.append(Element(
            id=str(len(out) + 1), kind="uia", type=SHORT.get(it["type"], it["type"]),
            name=" ".join(str(it.get("name") or "").split())[:80],
            rect=tuple(it["rect"]), value=str(it.get("value") or "")[:60],
            focused=bool(it.get("focused")), ref=it.get("ref")))
    return out


def ocr_elements(lines: list[dict], offset: tuple[int, int] = (0, 0), start: int = 1) -> list[Element]:
    """OCR lines -> clickable elements. lines: [{text, words:[{text, bounding_rect:{x,y,width,height}}]}]"""
    ox, oy = offset
    out: list[Element] = []
    for line in lines:
        words = line.get("words") or []
        text = " ".join(str(line.get("text") or "").split())
        if not words or not text:
            continue
        xs = [w["bounding_rect"]["x"] for w in words]
        ys = [w["bounding_rect"]["y"] for w in words]
        x2 = [w["bounding_rect"]["x"] + w["bounding_rect"]["width"] for w in words]
        y2 = [w["bounding_rect"]["y"] + w["bounding_rect"]["height"] for w in words]
        rect = (int(min(xs)) + ox, int(min(ys)) + oy, int(max(x2)) + ox, int(max(y2)) + oy)
        out.append(Element(id=f"t{start + len(out)}", kind="ocr", type="Text", name=text[:80], rect=rect))
        if len(out) >= MAX_ITEMS:
            break
    return out


def format_snapshot(title: str, process: str, monitor: int, elements: list[Element], source: str) -> str:
    head = f'Window: "{title}" ({process}) on monitor {monitor}. Seen via {source}. Ids are valid until the next observe.'
    if not elements:
        return head + "\n(no controls found; try read_screen, or look as a last resort)"
    lines = [head]
    for e in elements:
        extra = []
        if e.value and e.value != e.name:
            extra.append(f"value={e.value!r}")
        if e.focused:
            extra.append("focused")
        lines.append(f'[{e.id}] {e.type} "{e.name}"' + (f" ({', '.join(extra)})" if extra else ""))
    return "\n".join(lines)


def mean_hash(img) -> int:
    """16x16 grayscale mean hash of a PIL image."""
    small = img.convert("L").resize((16, 16))
    px = list(small.tobytes())  # 8-bit grayscale, one byte per pixel
    avg = sum(px) / len(px)
    bits = 0
    for p in px:
        bits = (bits << 1) | (p > avg)
    return bits


def monitor_of(rect, monitors: list[tuple[int, int, int, int]]) -> int:
    """1-based index of the monitor containing the rect's center (1 if none)."""
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    for i, (l, t, r, b) in enumerate(monitors, 1):
        if l <= cx < r and t <= cy < b:
            return i
    return 1


def changed(h1: int, h2: int, threshold: int = 4) -> bool:
    return bin(h1 ^ h2).count("1") > threshold


def is_sensitive(*labels: str) -> bool:
    return any(SENSITIVE.search(l or "") for l in labels)


def needs_confirm(label: str) -> bool:
    return bool(NEEDS_CONFIRM.search(label or ""))


KEYS = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def parse_keys(combo: str) -> list[int]:
    """'ctrl+shift+t' -> [VK_CONTROL, VK_SHIFT, 'T']. Raises ValueError on unknown keys."""
    out = []
    for k in combo.lower().replace(" ", "").split("+"):
        if k in KEYS:
            out.append(KEYS[k])
        elif len(k) == 1 and k.isalnum():
            out.append(ord(k.upper()))
        else:
            raise ValueError(f"unknown key '{k}'")
    if not out:
        raise ValueError("no keys")
    return out


# ---------------------------------------------------------------- Windows side
class Hands:
    """All methods must run on the same thread (the brain's worker thread)."""

    def __init__(self, vision=None, own_pids: set[int] | None = None) -> None:
        self.vision = vision                 # callable(image, question) -> str, or None
        self.own_pids = own_pids or {os.getpid()}
        self.elements: dict[str, Element] = {}
        self.window: tuple[int, str, str] | None = None   # (hwnd, title, process)
        self._tls = threading.local()

    # --- plumbing ---
    def _auto(self):
        import uiautomation as auto
        if not getattr(self._tls, "init", None):
            self._tls.init = auto.UIAutomationInitializerInThread()
        return auto

    def _target(self, window: str | None) -> tuple[int, str, str]:
        from . import winctl
        wins = [w for w in winctl.top_windows()
                if winctl.window_pid(w[0]) not in self.own_pids
                and w[1] not in ("Program Manager", "Windows Input Experience", "")]
        if window:
            q = window.lower().replace(" ", "")
            for w in wins:
                if q in w[2].lower().replace(" ", "") or q in w[1].lower().replace(" ", ""):
                    return w
            raise LookupError(f"no open window matches '{window}'")
        fg = winctl.foreground()
        for w in wins:
            if w[0] == fg:
                return w
        if not wins:
            raise LookupError("no windows open")
        return wins[0]   # Capri's own console was in front: use the window behind it

    def _rect(self, hwnd: int) -> tuple[int, int, int, int]:
        import ctypes
        import ctypes.wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def _grab(self, rect):
        from PIL import ImageGrab
        return ImageGrab.grab(bbox=rect, all_screens=True)

    def _monitor(self, rect) -> int:
        from . import winctl
        return monitor_of(rect, winctl.monitors())

    # --- seeing ---
    def list_windows(self) -> str:
        from . import winctl
        rows = []
        for hwnd, title, proc in winctl.top_windows():
            if winctl.window_pid(hwnd) in self.own_pids or not title or title == "Program Manager":
                continue
            rows.append(f'- "{title}" ({proc}) monitor {self._monitor(self._rect(hwnd))}')
        return "\n".join(rows[:25]) or "no windows"

    def observe(self, window: str | None = None) -> str:
        auto = self._auto()
        hwnd, title, proc = self._target(window)
        self.window = (hwnd, title, proc)
        root = auto.ControlFromHandle(hwnd)
        raw, end = [], time.monotonic() + WALK_BUDGET_S
        for ctrl, depth in auto.WalkControl(root, maxDepth=30):
            if time.monotonic() > end:
                break
            try:
                typ = ctrl.ControlTypeName
                if typ not in INTERACTIVE and typ not in READABLE:
                    continue
                br = ctrl.BoundingRectangle
                value = ""
                if typ in ("EditControl", "ComboBoxControl"):
                    vp = ctrl.GetPattern(auto.PatternId.ValuePattern)
                    value = "" if vp is None or ctrl.IsPassword else (vp.Value or "")
                raw.append({"name": ctrl.Name, "type": typ, "rect": (br.left, br.top, br.right, br.bottom),
                            "enabled": ctrl.IsEnabled, "offscreen": ctrl.IsOffscreen,
                            "focused": ctrl.HasKeyboardFocus, "value": value, "ref": ctrl})
            except Exception:
                continue
        els = pick_elements(raw)
        source = "UI Automation"
        if len([e for e in els if e.type != "Text"]) < MIN_USEFUL:
            ocr = self._ocr_elements(hwnd, start=1)
            if len(ocr) > len(els):
                els = els + ocr
                source = "UI Automation + on-screen text (OCR)"
        self.elements = {e.id: e for e in els}
        return format_snapshot(title, proc, self._monitor(self._rect(hwnd)), els, source)

    def _ocr_lines(self, hwnd: int):
        from winocr import recognize_pil_sync
        rect = self._rect(hwnd)
        img = self._grab(rect)
        res = recognize_pil_sync(img, "en")
        return res.get("lines") or [], (rect[0], rect[1])

    def _ocr_elements(self, hwnd: int, start: int) -> list[Element]:
        try:
            lines, offset = self._ocr_lines(hwnd)
        except Exception:
            return []
        return ocr_elements(lines, offset, start)

    def read_screen(self, window: str | None = None) -> str:
        hwnd, title, proc = self._target(window)
        lines, _ = self._ocr_lines(hwnd)
        text = "\n".join(" ".join(str(l.get("text") or "").split()) for l in lines)
        return f'Text in "{title}" ({proc}):\n{text[:3500] or "(no text found)"}'

    def look(self, question: str, window: str | None = None) -> str:
        if self.vision is None:
            return "vision unavailable"
        hwnd, title, _ = self._target(window)
        img = self._grab(self._rect(hwnd))
        img.thumbnail((1280, 1280))
        return self.vision(img, f'This is the "{title}" window. {question} Answer in one or two sentences.')

    # --- acting ---
    def _get(self, id: str) -> Element:
        e = self.elements.get(str(id).strip("[] "))
        if e is None:
            raise LookupError(f"no element [{id}]; call observe first (ids reset after each observe)")
        return e

    def _verified(self, action, what: str) -> str:
        rect = self._rect(self.window[0]) if self.window else None
        before = mean_hash(self._grab(rect)) if rect else None
        action()
        time.sleep(0.45)
        if before is None:
            return what
        after = mean_hash(self._grab(self._rect(self.window[0])))
        return what + ("; the screen changed" if changed(before, after) else
                       "; no visible change (maybe it needs another step, or try a different element)")

    def click(self, id: str, confirmed: bool = False) -> str:
        e = self._get(id)
        if needs_confirm(e.name) and not confirmed:
            return f'"{e.name}" looks irreversible. Ask the user to confirm, then call click again with confirmed=true.'
        auto = self._auto()

        def do() -> None:
            c = e.ref
            if e.kind == "uia" and c is not None:
                for pid, call in ((auto.PatternId.InvokePattern, lambda p: p.Invoke()),
                                  (auto.PatternId.TogglePattern, lambda p: p.Toggle()),
                                  (auto.PatternId.SelectionItemPattern, lambda p: p.Select()),
                                  (auto.PatternId.ExpandCollapsePattern, lambda p: p.Expand())):
                    try:
                        p = c.GetPattern(pid)
                        if p is not None:
                            call(p)
                            return
                    except Exception:
                        continue
            self._mouse_click(*e.center)

        return self._verified(do, f'clicked [{e.id}] {e.type} "{e.name}"')

    def _mouse_click(self, x: int, y: int) -> None:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        pt = wt.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetCursorPos(x, y)
        u.mouse_event(0x0002, 0, 0, 0, 0)   # left down
        u.mouse_event(0x0004, 0, 0, 0, 0)   # left up
        time.sleep(0.05)
        u.SetCursorPos(pt.x, pt.y)           # put the user's cursor back

    def type_text(self, text: str, id: str | None = None, enter: bool = False) -> str:
        if id is not None:
            e = self._get(id)
            if is_sensitive(e.name) or (e.kind == "uia" and e.ref is not None and _is_password(e.ref)):
                return f'Refused: "{e.name}" looks like a password/PIN/card field. The user types those.'
            self.click(id)
        auto = self._auto()
        focused = auto.GetFocusedControl()
        if focused is not None and (_is_password(focused) or is_sensitive(focused.Name)):
            return "Refused: the focused field is a password/PIN/card field. The user types those."
        escaped = text.replace("{", "{{}").replace("}", "{}}")  # uiautomation SendKeys brace syntax
        return self._verified(lambda: auto.SendKeys(escaped + ("{Enter}" if enter else ""), interval=0.005,
                                                    waitTime=0.05),
                              f"typed {len(text)} characters" + (" and pressed Enter" if enter else ""))

    def press_keys(self, keys: str) -> str:
        from . import winctl
        vks = parse_keys(keys)
        if vks[-1] in (0x2E,) and 0x10 in vks:   # shift+delete = permanent delete
            return "Refused: shift+delete permanently deletes. Ask the user to do it."

        def do() -> None:
            for vk in vks:
                winctl.key_down(vk)
            for vk in reversed(vks):
                winctl.key_up(vk)
        return self._verified(do, f"pressed {keys}")

    def scroll(self, direction: str = "down", id: str | None = None, amount: int = 5) -> str:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        if id is not None:
            x, y = self._get(id).center
        else:
            hwnd = self.window[0] if self.window else self._target(None)[0]
            l, t, r, b = self._rect(hwnd)
            x, y = (l + r) // 2, (t + b) // 2
        delta = 120 * max(1, min(20, amount)) * (1 if direction == "up" else -1)

        def do() -> None:
            pt = wt.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            u.SetCursorPos(x, y)
            u.mouse_event(0x0800, 0, 0, delta, 0)   # wheel
            time.sleep(0.05)
            u.SetCursorPos(pt.x, pt.y)
        return self._verified(do, f"scrolled {direction}")


def _is_password(ctrl) -> bool:
    try:
        return bool(ctrl.IsPassword)
    except Exception:
        return False

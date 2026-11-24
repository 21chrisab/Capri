"""Tiny always-on-top status pill, bottom-center of the primary monitor.

States (OpenClaw-style): listening (dot pulses with mic level), thinking,
speaking, computer ("Capri is using the computer · Esc to stop"), hidden.
It never takes focus, so it can't steal your typing. Clicking it stops her.
Runs its own Tk thread; everyone else just calls set().
"""
from __future__ import annotations

import queue
import threading
from typing import Callable

STYLE = {
    "listening": ("#7aa2ff", "Listening"),
    "thinking": ("#c4a7ff", "Thinking"),
    "speaking": ("#7ee0b5", "Speaking"),
    "computer": ("#ffb86b", "Capri is using the computer · Esc to stop"),
}


class Overlay:
    def __init__(self, on_click: Callable[[], None] | None = None,
                 level: Callable[[], float] | None = None) -> None:
        self.on_click = on_click
        self.level = level or (lambda: 0.0)
        self._q: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.state = "hidden"
        threading.Thread(target=self._run, name="overlay", daemon=True).start()

    def set(self, state: str, text: str | None = None) -> None:
        self.state = state
        self._q.put((state, text))

    # ------------------------------------------------------------ Tk thread
    def _run(self) -> None:
        import ctypes
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.93)
        bg = "#16161d"
        root.configure(bg=bg)
        frame = tk.Frame(root, bg=bg, padx=14, pady=7)
        frame.pack()
        dot = tk.Canvas(frame, width=14, height=14, bg=bg, highlightthickness=0)
        dot.pack(side="left", padx=(0, 8))
        oval = dot.create_oval(3, 3, 11, 11, fill="#7aa2ff", outline="")
        label = tk.Label(frame, text="", fg="#e8e8f0", bg=bg, font=("Segoe UI", 10))
        label.pack(side="left")
        for w in (root, frame, dot, label):
            w.bind("<Button-1>", lambda e: self.on_click and self.on_click())
        root.withdraw()

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

        def place() -> None:
            root.update_idletasks()
            w, h = root.winfo_reqwidth(), root.winfo_reqheight()
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"+{(sw - w) // 2}+{sh - h - 70}")

        state = {"name": "hidden", "t": 0}

        def tick() -> None:
            try:
                while True:
                    name, text = self._q.get_nowait()
                    state["name"] = name
                    if name == "hidden":
                        root.withdraw()
                    else:
                        color, default = STYLE.get(name, STYLE["thinking"])
                        dot.itemconfig(oval, fill=color)
                        label.config(text=text or default)
                        place()
                        root.deiconify()
                        root.attributes("-topmost", True)
            except queue.Empty:
                pass
            state["t"] += 1
            name = state["name"]
            if name == "listening":
                r = 3 + min(4, int(self.level() * 40))          # pulse with mic level
                dot.coords(oval, 7 - r, 7 - r, 7 + r, 7 + r)
            elif name in ("thinking", "computer"):
                r = 3 + (state["t"] // 3) % 3                    # slow breathing
                dot.coords(oval, 7 - r, 7 - r, 7 + r, 7 + r)
            else:
                dot.coords(oval, 3, 3, 11, 11)
            root.after(60, tick)

        root.after(60, tick)
        root.mainloop()

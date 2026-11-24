"""Passive listening: say "Capri" and she's on; she stays on until you go quiet for a while.

  asleep -> every bit of speech the VAD finds is transcribed locally and checked for "Capri".
            Nothing leaves the PC. "Capri, open Spotify" wakes her AND runs the command;
            "Capri?" alone wakes her with a quick "Yeah?".
  awake  -> everything you say is a turn, no name needed. After `idle_timeout_s` with no
            talking (counted from when she finishes her answer), she goes back to sleep.

No echo cancellation yet (that's later), so while she is talking she ignores what the mic
hears (otherwise she'd answer herself), except her name: "Capri, stop" still interrupts.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import TYPE_CHECKING

import numpy as np

from .timing import TurnTimer
from .vad import FRAME, Segmenter, SileroVAD, find_wake

if TYPE_CHECKING:
    from .main import Capri

ECHO_TAIL_S = 0.4       # after she stops talking, ignore the mic this long (room echo)


class PassiveListener:
    def __init__(self, app: "Capri", cfg: dict) -> None:
        c = cfg.get("listen", {})
        self.app = app
        self.idle_timeout = float(c.get("idle_timeout_s", 10))
        self.words = tuple(w.lower() for w in c.get("wake_words", ["capri"]))
        self.end_silence = float(c.get("end_silence_s", 0.7))
        self.vad = SileroVAD()
        self.seg = Segmenter(threshold=float(c.get("vad_threshold", 0.5)), end_silence_s=self.end_silence)
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self.awake = False
        self.last_activity = 0.0
        self._buf = np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.app.mic.tap = self.q
        threading.Thread(target=self._run, name="passive", daemon=True).start()

    def wake(self) -> None:
        if not self.awake:
            self.awake = True
            print("\r[awake: just talk. I'll sleep after "
                  f"{self.idle_timeout:.0f}s of quiet]", flush=True)
        self.last_activity = time.monotonic()
        self.app._show("listening")

    def sleep(self) -> None:
        if self.awake:
            self.awake = False
            print("\r[asleep: say \"Capri\" to wake me]", flush=True)
            if self.app.overlay is not None and self.app.overlay.state == "listening":
                self.app._show("hidden")

    # ------------------------------------------------------------ loop
    def _run(self) -> None:
        while True:
            try:
                chunk = self.q.get(timeout=0.1)
            except queue.Empty:
                chunk = None
            if chunk is not None:
                if self.app.hotkey_active:          # the hotkey owns the mic right now
                    self._buf = np.zeros(0, dtype=np.float32)
                    self.seg.reset()
                else:
                    self._buf = np.concatenate([self._buf, chunk])
                    while len(self._buf) >= FRAME:
                        frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
                        utt = self.seg.push(frame, self.vad(frame))
                        if utt is not None:
                            try:
                                self._utterance(utt)
                            except Exception as e:
                                print(f"\r[listen error] {e!r}", flush=True)
            self._check_idle()

    def _her_turn(self) -> bool:
        v = self.app.voice
        return v.speaking or (time.monotonic() - getattr(v, "quiet_since", 0.0) < ECHO_TAIL_S)

    def _utterance(self, audio: np.ndarray) -> None:
        timer = TurnTimer()                      # t0 = the VAD decided you stopped talking
        echo_risk = self._her_turn()
        if not self.awake and not echo_risk and len(audio) > 16000 * 8:
            return                               # long monologue while asleep: not for her
        text = self.app.transcribe(audio)
        timer.mark("stt_final")
        if not text:
            return
        heard, rest = find_wake(text, self.words)
        if echo_risk:
            if not heard:
                return                           # probably her own voice coming back through the mic
            self.app.gen.bump()                  # "Capri, stop" / "Capri, wait..." interrupts her
        elif not self.awake and not heard:
            return                               # asleep and not for her
        if heard:
            self.wake()
            if len(rest.split()) < 1 or rest.lower().strip(" .!?") in ("hey", "yo", "hi"):
                self.app.voice.ack("Yeah?")      # just her name: she answers and listens
                self.last_activity = time.monotonic()
                return
            text = rest
        self.last_activity = time.monotonic()
        timer.extra["source"] = "passive"
        self.app.submit_text(text, timer)

    def _check_idle(self) -> None:
        if not self.awake:
            return
        a = self.app
        if a.busy or a.voice.speaking or self.seg.active:
            self.last_activity = time.monotonic()  # the clock only runs while nobody is talking
            return
        if time.monotonic() - self.last_activity > self.idle_timeout:
            self.sleep()

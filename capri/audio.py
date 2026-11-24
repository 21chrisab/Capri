"""Microphone capture. M1: record while the hotkey is held.

The input stream stays open the whole time so pressing the hotkey costs
nothing. A 0.3 s pre-roll ring keeps the first syllable you say while
your fingers are still landing on the keys.
(M3 adds echo cancellation, Silero VAD and Smart Turn here.)
"""
from __future__ import annotations

import collections
import threading

import numpy as np
import sounddevice as sd


def find_device(name: str, kind: str) -> int | None:
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d[f"max_{kind}_channels"] > 0:
            return i
    raise ValueError(f"No {kind} device matching '{name}'. Run: python -m capri devices")


class Mic:
    def __init__(self, sample_rate: int = 16000, device: str = "", preroll_s: float = 0.3) -> None:
        self.sr = sample_rate
        self.block = int(sample_rate * 0.02)  # 20 ms blocks
        self._pre = collections.deque(maxlen=max(1, int(preroll_s / 0.02)))
        self._rec: list[np.ndarray] | None = None
        self._lock = threading.Lock()
        self.level = 0.0   # RMS of the latest 20 ms block, for the overlay
        self.tap = None    # queue.Queue: passive listening gets every block here
        self.stream = sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32",
                                     blocksize=self.block, device=find_device(device, "input"),
                                     callback=self._cb)

    def _cb(self, indata, frames, t, status) -> None:
        chunk = indata[:, 0].copy()
        self.level = float(np.sqrt(np.mean(chunk * chunk)))
        if self.tap is not None:
            try:
                self.tap.put_nowait(chunk)
            except Exception:
                pass   # listener fell behind: drop a block rather than stall the audio thread
        with self._lock:
            if self._rec is not None:
                self._rec.append(chunk)
            else:
                self._pre.append(chunk)

    def start(self) -> None:
        self.stream.start()

    def begin(self) -> None:
        with self._lock:
            self._rec = list(self._pre)
            self._pre.clear()

    def end(self) -> np.ndarray:
        with self._lock:
            rec, self._rec = self._rec or [], None
        return np.concatenate(rec) if rec else np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()

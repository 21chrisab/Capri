"""Capri's voice: Kokoro (kokoro-onnx) af_bella, streamed sentence by sentence.

- Short acks are rendered once at startup and kept in memory, so they play
  with zero synthesis time.
- Everything said is tagged with a generation. stop() or a newer generation
  cuts playback within one 20 ms block.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from .audio import find_device
from .config import ROOT
from .gen import Generation

SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE.split(text.strip()) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _model_dir(cfg: dict) -> Path:
    p = Path(cfg["voice"].get("model_dir", "models")).expanduser()
    return p if p.is_absolute() else ROOT / p


def _pick_provider(cfg: dict, available: list[str]) -> str:
    if sys.platform == "win32":
        gpu = "CUDAExecutionProvider" in available
        if not gpu:
            print("  Voice is running on CPU (slow). Run setup.ps1 to install onnxruntime-gpu.", flush=True)
        return "CUDAExecutionProvider" if gpu else "CPUExecutionProvider"
    if sys.platform == "darwin":
        want = cfg["voice"].get("provider", "auto").lower()
        if want == "coreml":
            if "CoreMLExecutionProvider" in available:
                return "CoreMLExecutionProvider"
            print("  CoreMLExecutionProvider unavailable; using CPUExecutionProvider.", flush=True)
        return "CPUExecutionProvider"
    return "CUDAExecutionProvider" if "CUDAExecutionProvider" in available else "CPUExecutionProvider"


class Voice:
    def __init__(self, cfg: dict, gen: Generation) -> None:
        import os

        import onnxruntime as rt
        if sys.platform == "win32" and hasattr(rt, "preload_dlls"):  # onnxruntime-gpu: load CUDA/cuDNN from the nvidia-* wheels
            try:
                rt.preload_dlls()
            except Exception:
                pass
        available = rt.get_available_providers()
        self.gpu = "CUDAExecutionProvider" in available
        # Pick the provider explicitly: otherwise kokoro-onnx also tries TensorRT and prints a wall of errors.
        os.environ.setdefault("ONNX_PROVIDER", _pick_provider(cfg, available))
        from kokoro_onnx import Kokoro
        v = cfg["voice"]
        mdir = _model_dir(cfg)
        self.kokoro = Kokoro(str(mdir / "kokoro-v1.0.onnx"), str(mdir / "voices-v1.0.bin"))
        self.voice, self.speed = v["voice"], v.get("speed", 1.0)
        self.gen = gen
        self.sr = 24000
        self.out_device = find_device(cfg["audio"].get("output_device", ""), "output")
        self._q: queue.Queue[tuple[int, np.ndarray, threading.Event | None]] = queue.Queue()
        self.on_state = None   # callable(bool speaking), set by main for the overlay
        self.speaking = False
        self.quiet_since = 0.0
        self.acks: dict[str, np.ndarray] = {a: self.render(a) for a in v.get("acks", [])}
        threading.Thread(target=self._player, name="voice", daemon=True).start()

    def render(self, text: str) -> np.ndarray:
        samples, sr = self.kokoro.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        self.sr = sr
        return samples.astype(np.float32)

    def _player(self) -> None:
        """Keeps the speaker stream fed with silence when idle.

        Letting the output stream run dry makes many Windows audio drivers sleep the
        device; when sound comes back the first ~100 ms play wrong (quiet, warbly or
        pitched down) while the driver/resampler ramps back up. Feeding continuous
        silence keeps it warm, and a 10 ms fade-in removes the click on each start.
        """
        block = int(self.sr * 0.02)
        silence = np.zeros(block, dtype=np.float32)
        fade = np.linspace(0.0, 1.0, int(self.sr * 0.01), dtype=np.float32)
        idle_blocks = 0
        with sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32", device=self.out_device,
                             blocksize=block) as out:
            while True:
                try:
                    g, audio, started = self._q.get_nowait()
                except queue.Empty:
                    out.write(silence)                    # blocks ~20 ms: paces this loop
                    idle_blocks += 1
                    if self.speaking and idle_blocks >= 12:   # ~250 ms of nothing queued
                        self._set_speaking(False)
                    continue
                if not self.gen.is_current(g):
                    continue
                if idle_blocks > 0 and len(audio) > len(fade):
                    audio = audio.copy()
                    audio[: len(fade)] *= fade
                idle_blocks = 0
                if started is not None:
                    started.set()
                if not self.speaking:
                    self._set_speaking(True)
                for i in range(0, len(audio), block):
                    if not self.gen.is_current(g):
                        break
                    out.write(audio[i:i + block])

    def _set_speaking(self, on: bool) -> None:
        self.speaking = on
        if not on:
            self.quiet_since = time.monotonic()
        if self.on_state:
            try:
                self.on_state(on)
            except Exception:
                pass

    def ack(self, text: str | None = None, started: threading.Event | None = None) -> None:
        text = text or next(iter(self.acks), None)
        if text in self.acks:
            self._q.put((self.gen.value, self.acks[text], started))
        elif text:
            self.say(text, started)

    def say(self, text: str, started: threading.Event | None = None) -> None:
        """Synthesize sentence by sentence on a worker thread; first sentence plays ASAP."""
        g = self.gen.value

        def work() -> None:
            first = True
            for s in split_sentences(text):
                if not self.gen.is_current(g):
                    return
                self._q.put((g, self.render(s), started if first else None))
                first = False

        threading.Thread(target=work, name="tts", daemon=True).start()

    def utterance(self, started: threading.Event | None = None) -> "Utterance":
        """For streamed LLM text: feed() deltas, close() at the end. Speaks in order."""
        return Utterance(self, started)

    def stop(self) -> None:
        self.gen.bump()


CLEAN = re.compile(r"[*_#`>~]|\[(.*?)\]\(.*?\)")
BOUNDARY = re.compile(r"([.!?…]+[\"')\]]?)\s+")


def speakable(text: str) -> str:
    """Strip markdown the model might still emit; keep link text."""
    return CLEAN.sub(lambda m: m.group(1) or "", text)


class Utterance:
    """Sentence chunker + single render thread so streamed sentences never play out of order."""

    def __init__(self, voice: "Voice", started: threading.Event | None) -> None:
        self.voice, self.started = voice, started
        self.g = voice.gen.value
        self.buf = ""
        self.spoke = False
        self.text = ""
        self._q: queue.Queue[str | None] = queue.Queue()
        self._t = threading.Thread(target=self._render, name="utterance", daemon=True)
        self._t.start()

    def feed(self, delta: str) -> None:
        self.text += delta
        self.buf += speakable(delta)
        while True:
            m = BOUNDARY.search(self.buf)
            if m is None:
                if len(self.buf) > 220:  # very long sentence: break at a comma so audio starts sooner
                    cut = self.buf.rfind(", ", 0, 200)
                    if cut > 40:
                        self._push(self.buf[: cut + 1])
                        self.buf = self.buf[cut + 2:]
                        continue
                break
            self._push(self.buf[: m.end(1)])
            self.buf = self.buf[m.end():]

    def _push(self, s: str) -> None:
        s = s.strip()
        if s:
            self.spoke = True
            self._q.put(s)

    def close(self) -> None:
        self._push(self.buf)
        self.buf = ""
        self._q.put(None)

    def _render(self) -> None:
        first = True
        while (s := self._q.get()) is not None:
            if not self.voice.gen.is_current(self.g):
                continue
            audio = self.voice.render(s)
            self.voice._q.put((self.g, audio, self.started if first else None))
            first = False


def model_files_present(cfg: dict) -> bool:
    mdir: Path = _model_dir(cfg)
    return (mdir / "kokoro-v1.0.onnx").exists() and (mdir / "voices-v1.0.bin").exists()

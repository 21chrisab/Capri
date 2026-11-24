"""STT shoot-out: record a few phrases once, run every engine on the same audio.

    python -m capri bench-stt            # record 5 phrases with the hotkey, then compare
    python -m capri bench-stt --reuse    # re-run on bench/*.wav without recording
"""
from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np

from . import stt as stt_mod
from .config import ROOT
from .timing import pct

BENCH = ROOT / "bench"
PHRASES = [
    "Open Spotify and play my liked songs.",
    "What's the weather like tomorrow?",
    "Close this window.",
    "Turn the volume down a little.",
    "Remind me to call mom at six.",
]


def save_wav(path: Path, audio: np.ndarray, sr: int = 16000) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32767


def record(cfg: dict) -> None:
    from .audio import Mic
    from .hotkey import HoldHotkey
    BENCH.mkdir(exist_ok=True)
    mic = Mic(cfg["audio"]["sample_rate"], cfg["audio"].get("input_device", ""))
    mic.start()
    done = threading.Event()
    box: list[np.ndarray] = []
    hk = HoldHotkey(cfg["hotkey"]["keys"], mic.begin, lambda: (box.append(mic.end()), done.set()),
                    on_cancel=mic.end)
    hk.start()
    keys = "+".join(cfg["hotkey"]["keys"])
    for i, phrase in enumerate(PHRASES, 1):
        done.clear()
        print(f'[{i}/{len(PHRASES)}] Hold {keys} and say: "{phrase}"  (say it from where you normally sit)')
        done.wait()
        save_wav(BENCH / f"{i:02d}.wav", box[-1])
    hk.stop()
    mic.close()


def run(cfg: dict, reuse: bool = False) -> None:
    if not reuse or not list(BENCH.glob("*.wav")):
        record(cfg)
    clips = sorted(BENCH.glob("*.wav"))
    audios = [load_wav(p) for p in clips]
    s = cfg["stt"]
    engines = {
        "faster-whisper": lambda: stt_mod.FasterWhisperSTT(s.get("whisper_model", "distil-small.en"),
                                                           s.get("whisper_device", "cuda")),
        "moonshine": lambda: stt_mod.MoonshineSTT(s.get("moonshine_arch", "medium-streaming")),
    }
    summary = []
    for name, factory in engines.items():
        print(f"\n=== {name} ===")
        try:
            eng = factory()
            eng.warmup()
        except Exception as e:
            print(f"  unavailable: {e!r}")
            continue
        times = []
        for clip, audio, phrase in zip(clips, audios, PHRASES):
            text, ms = stt_mod.timed(eng, audio)
            times.append(ms)
            print(f"  {clip.name}  {ms:6.0f} ms  heard: {text!r}\n{'':>22}said:  {phrase!r}")
        summary.append((name, pct(times, 0.5), max(times)))
    print("\nengine            p50 ms   max ms")
    for name, p50, mx in summary:
        print(f"{name:<16}{p50:>8.0f}{mx:>9.0f}")
    print("\nPick the one that heard you right AND is fastest, then set [stt] engine in config.toml.")

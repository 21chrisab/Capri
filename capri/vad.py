"""Voice activity detection (Silero, ONNX, CPU) + speech segmenter + wake-word matcher.

All local. The segmenter turns a stream of 512-sample frames into finished
utterances: speech starts when the VAD is confident, ends after `end_silence_s`
of quiet, and keeps 300 ms of audio from before the start so first syllables
aren't clipped.
"""
from __future__ import annotations

import difflib
import re
from collections import deque
from pathlib import Path

import numpy as np

FRAME = 512                 # samples per VAD step at 16 kHz (32 ms)
SR = 16000


def find_model() -> Path:
    """models/silero_vad.onnx, else the copy inside the pip package 'silero-vad' (installed with --no-deps)."""
    from .config import ROOT
    local = ROOT / "models" / "silero_vad.onnx"
    if local.exists():
        return local
    import importlib.util
    spec = importlib.util.find_spec("silero_vad")
    if spec and spec.submodule_search_locations:
        p = Path(list(spec.submodule_search_locations)[0]) / "data" / "silero_vad.onnx"
        if p.exists():
            return p
    raise FileNotFoundError("silero_vad.onnx not found. Rerun setup.ps1.")


class SileroVAD:
    """Silero v5: input = 64-sample context + 512 new samples, recurrent state (2, 1, 128)."""

    def __init__(self, path: Path | None = None) -> None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path or find_model()), sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame.reshape(1, -1).astype(np.float32)], axis=1)
        out, self.state = self.sess.run(None, {"input": x, "state": self.state, "sr": np.array(SR, dtype=np.int64)})
        self.context = x[:, -64:]
        return float(out[0][0])


class Segmenter:
    """Feed 512-sample frames with their speech probability; get finished utterances back."""

    def __init__(self, threshold: float = 0.5, end_silence_s: float = 0.7, min_speech_s: float = 0.3,
                 max_speech_s: float = 15.0, preroll_s: float = 0.3) -> None:
        self.on, self.off = threshold, max(0.15, threshold - 0.15)   # hysteresis
        self.end_frames = int(end_silence_s * SR / FRAME)
        self.min_frames = int(min_speech_s * SR / FRAME)
        self.max_frames = int(max_speech_s * SR / FRAME)
        self.pre: deque[np.ndarray] = deque(maxlen=max(1, int(preroll_s * SR / FRAME)))
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.buf: list[np.ndarray] = []
        self.silent = 0
        self.voiced = 0

    def set_end_silence(self, seconds: float) -> None:
        self.end_frames = max(3, int(seconds * SR / FRAME))

    def push(self, frame: np.ndarray, prob: float) -> np.ndarray | None:
        if not self.active:
            self.pre.append(frame)
            if prob >= self.on:
                self.active = True
                self.buf = list(self.pre)
                self.pre.clear()
                self.silent, self.voiced = 0, 1
            return None
        self.buf.append(frame)
        if prob >= self.off:
            self.voiced += 1
            self.silent = 0
        else:
            self.silent += 1
        if self.silent >= self.end_frames or len(self.buf) >= self.max_frames:
            audio = np.concatenate(self.buf)
            enough = self.voiced >= self.min_frames
            self.reset()
            return audio if enough else None
        return None


# ---------------------------------------------------------------- wake word
WAKE_RE = re.compile(r"\b(?:c|k)a+p+(?:r+|l+)(?:i+|ee|ie|y|is|ey|e)\b|\bcap\s?ree\b|\bcopri\b", re.I)


def find_wake(text: str, words: tuple[str, ...] = ("capri",)) -> tuple[bool, str]:
    """Return (heard_wake_word, the rest of the sentence after it).

    Fuzzy on purpose: speech-to-text writes 'Capri' as Capri / Kapri / Capree / Caprie...
    'Hey Capri, open Spotify' -> (True, 'open Spotify').  'Capri?' -> (True, '').
    """
    m = WAKE_RE.search(text)
    if m is None:
        tokens = re.findall(r"[A-Za-z']+", text)
        for tok in tokens:
            if any(difflib.SequenceMatcher(None, tok.lower(), w).ratio() >= 0.8 for w in words):
                m = re.search(re.escape(tok), text)
                break
    if m is None:
        return False, ""
    rest = text[m.end():]
    rest = re.sub(r"^[\s,.!?:;\-–—]+", "", rest).strip()
    return True, rest

"""Speech-to-text behind one interface: transcribe(float32 16 kHz audio) -> str.

Two engines, chosen in config.toml after running `python -m capri bench-stt`:
- faster-whisper (distil-small.en on the RTX 3050, CPU fallback)
- moonshine (moonshine-voice, CPU). Default: in a CPU smoke test it heard
  "liked songs" correctly in ~430 ms where distil-small said "light songs"
  in ~1300 ms, and it leaves the GPU free for Kokoro.
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np


def _add_cuda_dlls() -> None:
    """pip's nvidia-cublas/cudnn wheels put DLLs in site-packages/nvidia/*/bin, off PATH."""
    if sys.platform != "win32":
        return
    try:
        import nvidia  # namespace package from the nvidia-* wheels
    except ImportError:
        return
    for base in nvidia.__path__:
        for sub in os.listdir(base):
            b = os.path.join(base, sub, "bin")
            if os.path.isdir(b):
                os.add_dll_directory(b)
                os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dlls()


class STT:
    name = "base"

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> str:  # pragma: no cover
        raise NotImplementedError

    def warmup(self) -> None:
        self.transcribe(np.zeros(16000, dtype=np.float32))


class FasterWhisperSTT(STT):
    name = "faster-whisper"

    def __init__(self, model: str = "distil-small.en", device: str = "cuda") -> None:
        from faster_whisper import WhisperModel
        try:
            self.model = WhisperModel(model, device=device, compute_type="float16" if device == "cuda" else "int8")
            self.device = device
            self.transcribe(np.zeros(16000, dtype=np.float32))  # missing CUDA DLLs only fail here
        except Exception as e:
            if device != "cpu":
                print(f"  GPU speech-to-text unavailable ({type(e).__name__}); using CPU.", flush=True)
            self.model = WhisperModel(model, device="cpu", compute_type="int8")
            self.device = "cpu"

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> str:
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                            without_timestamps=True, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()


class MoonshineSTT(STT):
    name = "moonshine"

    def __init__(self, arch: str = "medium-streaming") -> None:
        import moonshine_voice as mv
        self.mv = mv
        self.arch = arch
        self.transcriber = self._build(mv, arch)

    @staticmethod
    def _build(mv, arch: str):
        # Preferred: a model helper + Transcriber(model_path=..., model_arch=...) as in the docs.
        getter = getattr(mv, "get_model_for_language", None)
        if getter is not None:
            enum = mv.ModelArch[arch.upper().replace("-", "_")]  # "medium-streaming" -> MEDIUM_STREAMING
            model_path, model_arch = getter("en", enum)
            return mv.Transcriber(model_path=model_path, model_arch=model_arch)
        raise RuntimeError(
            "This moonshine-voice version has no get_model_for_language(); "
            "check https://moonshine-voice.readthedocs.io and adjust MoonshineSTT._build.")

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> str:
        # Verified against moonshine-voice 0.1.6: returns a Transcript with .lines[].text
        result = self.transcriber.transcribe_without_streaming(audio.astype(np.float32), sr)
        return " ".join(line.text.strip() for line in result.lines).strip()


def make(cfg: dict) -> STT:
    s = cfg["stt"]
    if s["engine"] == "moonshine":
        return MoonshineSTT(s.get("moonshine_arch", "medium-streaming"))
    return FasterWhisperSTT(s.get("whisper_model", "distil-small.en"), s.get("whisper_device", "cuda"))


def timed(stt: STT, audio: np.ndarray) -> tuple[str, float]:
    t = time.perf_counter()
    text = stt.transcribe(audio)
    return text, (time.perf_counter() - t) * 1000

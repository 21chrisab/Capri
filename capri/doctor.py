"""capri.bat / capri.sh doctor: checks every part Capri needs, one line each, with the fix."""
from __future__ import annotations

import importlib
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from .config import ROOT

OK, BAD, WARN = "  OK  ", " FAIL ", " WARN "


def line(status: str, what: str, detail: str = "") -> bool:
    print(f"[{status}] {what}" + (f"  ->  {detail}" if detail else ""), flush=True)
    return status != BAD


def run() -> int:
    print(f"Capri doctor  ({ROOT})\n", flush=True)
    ok = True
    is_mac = platform.system() == "Darwin"
    is_win = platform.system() == "Windows"
    setup_hint = "rerun ./setup.sh" if is_mac else "rerun setup.ps1"

    v = sys.version_info
    ok &= line(OK if v >= (3, 11) else BAD, f"Python {v.major}.{v.minor}",
               "" if v >= (3, 11) else f"install Python 3.12 and {setup_hint}")
    ok &= line(OK if (is_win or is_mac) else BAD, f"OS: {platform.system()} {platform.release()}")

    mods = [("numpy", ""), ("sounddevice", "mic + speakers"), ("moonshine_voice", "speech-to-text"),
            ("kokoro_onnx", "voice"), ("onnxruntime", "voice runtime"), ("google.genai", "brain"),
            ("psutil", "process info"), ("PIL", "screenshots/overlay")]
    if is_mac:
        mods.append(("pynput", "hotkey"))
    else:
        mods.extend([("uiautomation", "seeing apps"), ("winocr", "reading screen text")])

    for mod, why in mods:
        t = time.perf_counter()
        try:
            importlib.import_module(mod)
            ok &= line(OK, f"import {mod}", f"{(time.perf_counter() - t) * 1000:.0f} ms")
        except Exception as e:
            ok &= line(BAD, f"import {mod} ({why})", f"{type(e).__name__}: {e}. {setup_hint.capitalize()}")

    if is_win:
        try:
            import onnxruntime as rt
            if hasattr(rt, "preload_dlls"):
                try:
                    rt.preload_dlls()
                except Exception:
                    pass
            gpu = "CUDAExecutionProvider" in rt.get_available_providers()
            line(OK if gpu else WARN, "GPU voice (CUDA)",
                 "on" if gpu else "off: voice will be slow. Rerun setup.ps1 (installs onnxruntime-gpu)")
        except Exception:
            pass

    if is_mac:
        try:
            r = subprocess.run(["osascript", "-e", "return 1"], capture_output=True, text=True, timeout=5)
            ok &= line(OK if r.returncode == 0 and r.stdout.strip() == "1" else BAD, "osascript")
        except Exception as e:
            ok &= line(BAD, "osascript", repr(e))
        line(OK if Path("/Applications/Spotify.app").exists() else WARN, "Spotify.app",
             "installed" if Path("/Applications/Spotify.app").exists() else "not found at /Applications/Spotify.app")

    from .config import load
    try:
        cfg = load()
        ok &= line(OK, "config.toml")
    except Exception as e:
        line(BAD, "config.toml", repr(e))
        return 1

    from .voice import _model_dir
    mdir = _model_dir(cfg)
    for f in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        p = mdir / f
        ok &= line(OK if p.exists() and p.stat().st_size > 1_000_000 else BAD, f"models/{f}",
                   "" if p.exists() else f"missing: {setup_hint} (downloads it)")

    try:
        from .vad import SileroVAD, find_model
        import numpy as np
        v = SileroVAD()
        v(np.zeros(512, dtype=np.float32))
        ok &= line(OK, "voice detection (Silero VAD)", str(find_model().name))
    except Exception as e:
        ok &= line(BAD, "voice detection (Silero VAD)", f"{type(e).__name__}: {e}. {setup_hint.capitalize()}")

    from .handoff import find_claude
    exe = find_claude()
    if exe:
        try:
            v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
            line(OK, "Claude Code (handoff)", v or exe)
        except Exception as e:
            line(WARN, "Claude Code (handoff)", f"found but didn't run: {e}")
    else:
        install_cmd = ("curl -fsSL https://claude.ai/install.sh | bash" if is_mac
                       else "irm https://claude.ai/install.ps1 | iex")
        line(WARN, "Claude Code (handoff)", f"not installed: run  {install_cmd}"
             "  then run  claude  once to log in with your Claude Pro account")

    from .life import find as find_life
    life = find_life(cfg)
    line(OK if life else WARN, "life folder", str(life.root) if life else
         "not found: install Google Drive for desktop, put life/ in My Drive, or set [life] dir in config.toml")

    from .env import load_env
    load_env()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    line(OK if key else WARN, ".env GEMINI_API_KEY",
         f"set ({key[:6]}…)" if key else "empty: brain is off until you paste your key into .env")

    try:
        import sounddevice as sd
        di, do = sd.default.device
        devs = sd.query_devices()
        name = cfg["audio"].get("input_device") or (devs[di]["name"] if di is not None and di >= 0 else "none")
        ok &= line(OK if di is not None and di >= 0 else BAD, f"microphone: {name}")
        oname = devs[do]["name"] if do is not None and do >= 0 else "none"
        ok &= line(OK if do is not None and do >= 0 else BAD, f"speakers: {oname}")
        with sd.InputStream(samplerate=16000, channels=1, dtype="float32", device=None) as s:
            data, _ = s.read(8000)
        import numpy as np
        rms = float(np.sqrt(np.mean(data * data)))
        mic_hint = ("almost silent: check System Settings → Privacy & Security → Microphone / input device"
                    if is_mac else "almost silent: check Windows mic privacy settings / the right input device")
        line(OK if rms > 0.0005 else WARN, f"mic level {rms:.4f}", "" if rms > 0.0005 else mic_hint)
    except Exception as e:
        ok &= line(BAD, "audio devices", f"{type(e).__name__}: {e}")

    print("\nLoading speech-to-text (the first time downloads the model, ~1-2 min)...", flush=True)
    try:
        from . import stt as stt_mod
        t = time.perf_counter()
        s = stt_mod.make(cfg)
        s.warmup()
        ok &= line(OK, f"speech-to-text: {s.name}", f"ready in {time.perf_counter() - t:.1f} s")
    except Exception as e:
        ok &= line(BAD, "speech-to-text", f"{type(e).__name__}: {e}")

    if is_mac:
        print("\nmacOS permissions (System Settings → Privacy & Security):", flush=True)
        print("  - Microphone        → enable your Terminal app", flush=True)
        print("  - Input Monitoring  → enable your Terminal app (Option hold-to-talk)", flush=True)
        print("  - Accessibility     → enable your Terminal app (window controls & hotkey)", flush=True)

    ready_msg = ("All good. Run ./capri.sh and hold Option to talk." if is_mac
                 else "All good. Run capri.bat and hold Alt to talk.")
    print("\n" + (ready_msg if ok else f"Fix the FAIL lines above (usually: {setup_hint}), then try again."),
          flush=True)
    return 0 if ok else 1

"""python -m capri [run | doctor | budget | bench-stt [--reuse] | devices | say "text"]"""
from __future__ import annotations

import sys

from .config import load


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "run"
    cfg = load()
    if cmd == "run":
        from .main import run
        run()
    elif cmd == "doctor":
        from .doctor import run as doctor
        return doctor()
    elif cmd in ("budget", "--budget"):
        from .timing import report
        text, ok = report({kind: {k: float(v) for k, v in stages.items()}
                           for kind, stages in cfg.get("budget", {}).items()})
        print(text)
        return 0 if ok else 1
    elif cmd == "bench-stt":
        from .bench import run as bench
        bench(cfg, reuse="--reuse" in argv)
    elif cmd == "devices":
        import sounddevice as sd
        print(sd.query_devices())
    elif cmd == "say":
        import threading
        import time

        from .gen import Generation
        from .voice import Voice
        v = Voice(cfg, Generation())
        t = time.perf_counter()
        started = threading.Event()
        v.say(" ".join(argv[1:]) or "Hey, it's Capri.", started)
        started.wait(10)
        print(f"first audio after {(time.perf_counter() - t) * 1000:.0f} ms")
        time.sleep(4)
    else:
        print(__doc__)
        return 2
    return 0


def _guarded(argv: list[str]) -> int:
    """Any crash gets printed AND saved to logs/crash.log, so a closing window never hides it."""
    try:
        return main(argv)
    except KeyboardInterrupt:
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (print(e.code, flush=True) or 1)
    except Exception:
        import datetime
        import traceback

        from .config import ROOT
        tb = traceback.format_exc()
        print("\n" + tb, flush=True)
        log = ROOT / "logs" / "crash.log"
        log.parent.mkdir(exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n{tb}")
        return 1


if __name__ == "__main__":
    sys.exit(_guarded(sys.argv[1:]))

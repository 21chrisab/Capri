"""Capri v2 main loop. Hold the hotkey and talk.
Tier 1: instant commands (no AI). Tier 3: Gemini brain with tools and memory.
(Tier 2, learned shortcuts, arrives in M5.)

Threads:
  hotkey   - 100 Hz key poll, press/release/Esc
  mic      - sounddevice callback, always open
  worker   - STT + respond, one turn at a time
  voice    - playback (+ short-lived tts threads)
"""
from __future__ import annotations

import queue
import sys
import threading
import time

import numpy as np

from . import stt as stt_mod
from .audio import Mic
from .config import load
from .gen import Generation
from .hotkey import HoldHotkey
from .reflex import Reflex
from .router import Intent
from .timing import TurnTimer
from .voice import Voice, model_files_present


class Capri:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.gen = Generation()
        print("Loading speech-to-text (the first run downloads the model, about 1-2 minutes)...", flush=True)
        self.stt = stt_mod.make(cfg)
        self.stt.warmup()
        print(f"  {self.stt.name} ready ({getattr(self.stt, 'device', 'cpu')})", flush=True)
        if not model_files_present(cfg):
            hint = "./setup.sh" if sys.platform == "darwin" else "setup.ps1"
            raise SystemExit(f"Kokoro model files missing. Run {hint} first.")
        print("Loading voice...", flush=True)
        self.voice = Voice(cfg, self.gen)
        print("Indexing apps...", flush=True)
        from .actions import Actions
        from .sysapps import AppIndex
        apps = AppIndex()
        from .life import find as find_life
        self.life = find_life(cfg)
        print(f"  life folder: {self.life.root if self.life else 'not found (set [life] dir in config.toml)'}",
              flush=True)
        from .brain import Brain
        from .memory import Memory, memory_dir
        if sys.platform == "win32":
            from .hands import Hands
            self.hands = Hands()
        else:
            self.hands = None
        self.actions = Actions(self.voice, apps, life=self.life, hands=self.hands)
        self.reflex = Reflex(classify=self._local_classifier(cfg))
        from .handoff import Handoff, find_claude
        self.handoff = Handoff(cfg, on_done=self._handoff_done)
        self.brain = Brain(self.voice, self.gen, Memory(memory_dir(cfg)), apps=apps, hands=self.hands,
                           handoff=self.handoff, life=self.life)
        claude = find_claude()
        print(f"  claude handoff: {'ready (' + claude + ')' if claude else 'Claude Code not installed (see README)'}",
              flush=True)
        print(f"  brain: {'ready (' + self.brain.models[0] + ')' if self.brain.ready else 'no GEMINI_API_KEY in .env, brain off'}",
              flush=True)
        self.mic = Mic(cfg["audio"]["sample_rate"], cfg["audio"].get("input_device", ""))
        self.turns: queue.Queue[tuple[np.ndarray | None, TurnTimer, str | None]] = queue.Queue()
        self.busy = False
        self.hotkey_active = False
        from .web import TurnLog
        self.ui_state = "idle"             # mirrored to the dashboard sidebar
        self.turnlog = TurnLog()
        self.web = None
        self.stt_lock = threading.Lock()   # passive listener and worker share one STT model
        self.passive = None
        self.overlay = None
        if cfg.get("overlay", {}).get("enabled", True):
            if sys.platform == "win32":
                from .overlay import Overlay
                self.overlay = Overlay(on_click=self.on_esc, level=lambda: self.mic.level)
                self.voice.on_state = self._voice_state
                self.brain.tools.on_hands = lambda: self._show("computer")
            elif sys.platform == "darwin":
                print("  overlay: not on macOS yet", flush=True)
        self.hotkey = HoldHotkey(cfg["hotkey"]["keys"], self.on_press, self.on_release, self.on_esc,
                                 on_confirm=self.on_confirm, on_cancel=self.on_cancel)

    # --- handoff ---
    def _handoff_done(self, job) -> None:
        """A Claude job finished: say so out loud (after anything she's saying now)."""
        self.turnlog.add("capri", f"Claude {'finished' if job.status == 'done' else 'couldn’t finish'} "
                                  f"{job.title}. {job.summary}", via="handoff")
        print(f"\r[claude #{job.id} {job.status} in {job.age()}] {job.summary}\n  -> {job.result_path}", flush=True)
        if job.status == "done":
            self.voice.say(f"Claude finished {job.title}. {job.summary}")
        else:
            self.voice.say(f"Claude couldn't finish {job.title}. {job.summary}")

    @staticmethod
    def _local_classifier(cfg: dict):
        """The optional local model for the reflex tier ([reflex] local_model = "" turns it off)."""
        model = cfg.get("reflex", {}).get("local_model", "")
        if not model:
            return None
        from .classifier import LocalClassifier
        clf = LocalClassifier(model, timeout_s=float(cfg.get("reflex", {}).get("timeout_s", 1.5)))
        threading.Thread(target=clf.warm, name="reflex-warm", daemon=True).start()
        return clf

    # --- overlay ---
    def _show(self, state: str) -> None:
        self.ui_state = state
        if self.overlay is not None:
            self.overlay.set(state)

    def _idle_state(self) -> str:
        return "listening" if self.passive is not None and self.passive.awake else "hidden"

    def _voice_state(self, speaking: bool) -> None:
        if speaking:
            if self.overlay is not None and not self.hotkey_active:
                self._show("speaking")
        elif not self.busy and self.overlay is not None and not self.hotkey_active:
            self._show(self._idle_state())

    def transcribe(self, audio: np.ndarray) -> str:
        with self.stt_lock:
            return self.stt.transcribe(audio, self.cfg["audio"]["sample_rate"])

    def submit_text(self, text: str, timer: TurnTimer, via: str = "voice") -> None:
        """A turn that was already transcribed (passive listening) or typed in the dashboard."""
        timer.extra["via"] = via
        self.gen.bump()                    # a new turn always replaces whatever she was doing
        self.turns.put((None, timer, text))
        self._show("thinking")

    # --- hotkey callbacks (keep them tiny) ---
    def on_press(self) -> None:
        self.hotkey_active = True
        self.mic.begin()         # start recording right away so the first word isn't lost

    def on_confirm(self) -> None:
        self.gen.bump()          # barge-in: talking cancels whatever she was doing
        self._show("listening")

    def on_cancel(self) -> None:
        self.hotkey_active = False
        self.mic.end()           # it was Alt+Tab or a quick tap: throw the audio away
        if self.overlay is not None and self.overlay.state == "listening":
            self._show(self._idle_state())
        print("\r[listening]        ", end="", flush=True)

    def on_release(self) -> None:
        timer = TurnTimer()  # t0 = the moment you stopped talking; kind is set once routed
        self.hotkey_active = False
        self.turns.put((self.mic.end(), timer, None))
        self._show("thinking")

    def on_esc(self) -> None:
        self.gen.bump()
        if self.passive is not None:
            self.passive.sleep()       # Esc also ends a hands-free conversation
        self._show("hidden")

    # --- turn handling ---
    def handle(self, audio: np.ndarray | None, timer: TurnTimer, text: str | None = None) -> None:
        sr = self.cfg["audio"]["sample_rate"]
        g = self.gen.value
        if text is None:
            if audio is None or len(audio) < sr * self.cfg["audio"].get("min_seconds", 0.25):
                print("\r[too short]        ", flush=True)
                return
            text = self.transcribe(audio) if hasattr(self, "stt_lock") else self.stt.transcribe(audio, sr)
            timer.mark("stt_final")
            timer.extra.update(audio_s=round(len(audio) / sr, 2))
        timer.extra.update(engine=self.stt.name, text=text)
        if not text or not self.gen.is_current(g):
            print("\r[heard nothing]    ", flush=True)
            return
        log = getattr(self, "turnlog", None)       # absent in tests that build Capri without __init__
        if log is not None:
            log.add("you", text, via=timer.extra.get("via", "voice"))
        self.respond(text, timer)
        if log is not None:
            log.add("capri", timer.extra.get("reply") or _instant_label(timer.extra.get("intent", "")))
        row = timer.save()
        print(f"\ryou: {text}   [{row['kind']}{' ' + row['intent'] if 'intent' in row else ''}] "
              f"(stt {row.get('stt_final', 0):.0f} ms, first audio {row.get('first_audio', float('nan')):.0f} ms)",
              flush=True)

    def respond(self, text: str, timer: TurnTimer) -> None:
        # Tier 1: the reflex. Fixed actions only, never a made-up reply.
        if getattr(self, "reflex", None) is None:     # tests build Capri without __init__
            self.reflex = Reflex()
        intents = self.reflex.decide(text) if getattr(self, "actions", None) is not None else []
        if intents:
            timer.kind = "instant"
            timer.extra["intent"] = "+".join(i.name for i in intents)
            handled = False
            for intent in intents:
                if intent.name in ("app_open", "open_site") and self.reflex.recently_launched(str(intent.arg)):
                    # Guard rail: a repeated launch within seconds switches to it instead of opening a second copy.
                    intent = Intent("app_focus", intent.arg) if intent.name == "app_open" else None
                    if intent is None:
                        continue
                if self.actions.execute(intent, timer, quiet=handled):
                    self.reflex.note(intent)
                    handled = True
                    if intent.name == "life_today" and getattr(self, "life", None) is not None:
                        timer.extra["reply"] = self.life.today_summary()
            if handled:
                return
        # Tier 3: the Gemini brain.
        if getattr(self, "brain", None) is not None and self.brain.ready:
            timer.kind = "brain"
            timer.extra.pop("intent", None)
            try:
                reply = self.brain.think(text, timer)
                timer.extra["reply"] = reply[:200]
            except Exception as e:
                print(f"\r[brain error] {e!r}", flush=True)
                self.voice.say("My brain glitched. Try that again?")
            return
        # No brain (no API key): repeat what she heard so speech-to-text is easy to check.
        timer.kind = "echo"
        started = threading.Event()
        self.voice.say(f"Can't do that one yet. I heard: {text}", started)
        if started.wait(10):
            timer.mark("first_audio")

    # --- dashboard sidebar (capri/web.py) ---
    def web_state(self) -> dict:
        state = "speaking" if self.voice.speaking else self.ui_state
        if state == "hidden":
            state = "idle"
        return {"online": True, "state": state, "log": self.turnlog.snapshot(),
                "brain": bool(self.brain.ready), "life": self.life is not None}

    def web_say(self, text: str) -> None:
        self.submit_text(text, TurnTimer(), via="typed")

    def web_stop(self) -> None:
        self.on_esc()

    def web_rebuild(self) -> str:
        return self.life.dashboard(open_it=False) if self.life else "no life folder"

    def _start_web(self) -> None:
        wcfg = self.cfg.get("web", {})
        if not wcfg.get("enabled", True):
            return
        from .web import WebServer
        try:
            self.web = WebServer(self, self.life.root if self.life else None, int(wcfg.get("port", 8765))).start()
        except OSError as e:
            print(f"  dashboard server: couldn't start ({e})", flush=True)
            return
        if self.life is not None:
            self.life.dashboard_url = self.web.url

            def keep_fresh() -> None:          # rebuild now and every 15 min (Canvas + files)
                while True:
                    try:
                        self.life.dashboard(open_it=False)
                    except Exception as e:
                        print(f"\r[dashboard] {e!r}", flush=True)
                    time.sleep(15 * 60)
            threading.Thread(target=keep_fresh, name="dashboard", daemon=True).start()
        print(f"  dashboard: {self.web.url}", flush=True)

    def worker(self) -> None:
        while True:
            audio, timer, text = self.turns.get()
            self.busy = True
            try:
                self.handle(audio, timer, text)
            except Exception as e:  # never let one bad turn kill her
                print(f"\r[error] {e!r}", flush=True)
            finally:
                self.busy = False
                if self.passive is not None and self.passive.awake:
                    self.passive.last_activity = time.monotonic()
                if not self.voice.speaking and self.overlay is not None and not self.hotkey_active:
                    self._show(self._idle_state())

    def run(self) -> None:
        self.mic.start()
        threading.Thread(target=self.worker, name="worker", daemon=True).start()
        self._start_web()
        mode = self.cfg.get("listen", {}).get("mode", "both")
        keys = "+".join(k.capitalize() for k in self.cfg["hotkey"]["keys"])
        self.hotkey.start()               # always on: the hotkey and Esc work in every mode
        how = [f"hold {keys} and talk"]
        if mode in ("passive", "both"):
            from .listen import PassiveListener
            self.passive = PassiveListener(self, self.cfg)
            self.passive.start()
            how.append('say "Capri"')
        self.voice.ack("Yep.")
        print(f"\nCapri is up. {' or '.join(how).capitalize()}. Esc stops her. Ctrl+C quits.\n", flush=True)
        _tray(self)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.mic.close()


_LABELS = {"life_capture": "Added to your inbox.", "life_done": "Checked off.", "life_dashboard": "Opened the dashboard.",
           "app_open": "Opened it.", "app_close": "Closed it.", "app_focus": "Switched.", "stop": "Stopped.",
           "thanks": "Anytime.", "spotify_play": "Playing.", "media_pause": "Paused.", "media_play": "Resumed.",
           "media_next": "Skipped.", "media_prev": "Went back.", "mute": "Muted.", "unmute": "Unmuted."}


def _instant_label(intent: str) -> str:
    if not intent:
        return ""
    if intent.startswith("volume"):
        return "Volume changed."
    if intent.startswith("window"):
        return "Done."
    return _LABELS.get(intent, "Done.")


def _tray(app: Capri) -> None:
    """Optional tray icon with Quit. Skipped quietly if pystray isn't installed."""
    try:
        import os

        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=(120, 170, 255, 255))
    icon = pystray.Icon("Capri", img, "Capri", pystray.Menu(
        pystray.MenuItem("Stop talking (Esc)", lambda: app.gen.bump()),
        pystray.MenuItem("Quit", lambda: (icon.stop(), os._exit(0))),
    ))
    threading.Thread(target=icon.run, name="tray", daemon=True).start()


def run() -> None:
    from .env import load_env
    load_env()
    Capri(load()).run()

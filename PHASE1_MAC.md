# Capri on macOS — Phase 1 build spec

You are adding macOS (Apple Silicon) support to Capri, a Python 3.12 voice assistant that today runs only on
Windows. Read `README.md` and skim `capri/` first. **Windows behavior must not change.** All existing tests must
keep passing (`python -m pytest -q`, 164 tests today; they also run on Linux/macOS).

Phase 1 = everything except "hands" (seeing/clicking inside apps). On macOS Capri must: listen (hold-to-talk
hotkey + "Capri" wake word), transcribe, think (Gemini), speak (Kokoro), open/close/switch apps, control Spotify
and volume, use memory, the life folder, the dashboard server (`capri/web.py`) and Claude handoff.

## Ground rules
- Python 3.11–3.13. Only new dependency allowed: `pynput` (macOS only). Everything else via the standard library
  and `osascript` (AppleScript) through `subprocess.run([...], capture_output=True, text=True, timeout=...)`.
  Never pass user text into AppleScript without escaping it (`"` and `\`); write one helper `_osa(script) -> str`
  and one `_q(s) -> str` that returns a safely quoted AppleScript string literal.
- Keep modules importable on any OS (tests run on Linux). Import Windows/mac-only things lazily inside functions.
- Small, readable code in the existing style (type hints, short docstrings, no classes where functions do).
- Do not edit `capri/hands.py`, `capri/web.py`, `capri/life.py`, `capri/memory.py` internals beyond what's listed.

## 1. Platform switch: `capri/sysctl.py` and `capri/sysapps.py` (new)
- `sysctl.py`: `if sys.platform == "darwin": from .macctl import *` else `from .winctl import *` (keep `winctl`
  importable on Linux for tests as it is today). Export the same public names in both.
- `sysapps.py`: same idea for `apps` (Windows) vs `macapps` (mac). Public API both must provide:
  `App` (dataclass: `name: str`, `app_id: str`), `match(query, apps) -> App | None`, `launch(app)`, `AppIndex`
  (with `.find(query)` and `.apps`).
- Replace imports in `capri/actions.py`, `capri/tools.py`, `capri/main.py`: `from . import winctl` →
  `from . import sysctl as winctl` is NOT acceptable; rename call sites to `sysctl.` and `from .sysapps import ...`.

## 2. `capri/macctl.py` (new) — same public functions as `winctl.py`
Window "handles" on mac are **app names** (str). Implement:
| function | mac implementation |
|---|---|
| `media_play_pause/next/prev()` | Spotify AppleScript: `playpause`, `next track`, `previous track` (only if Spotify is running) |
| `volume_up(pct)`, `volume_down(pct)` | read `output volume of (get volume settings)`, add/subtract, clamp 0–100, `set volume output volume N` |
| `volume_set(pct)` | `set volume output volume N` |
| `mute_toggle()` | `set volume output muted (not (output muted of (get volume settings)))` |
| `top_windows() -> list[(handle, title, proc)]` | visible app processes from System Events (`background only is false`); title = front window name if available else app name; proc = app name |
| `find_window(query) -> str | None` | fuzzy-match query against running visible app names (reuse `macapps.match` logic) |
| `focus(h)` | `tell application "<h>" to activate` |
| `foreground() -> str` | name of the frontmost process (System Events) |
| `close(h)` | close the front window of that app (System Events: click the close button / `keystroke "w" using command down` after activating). Do not quit the app. |
| `minimize(h)` | set the front window's `miniaturized`/`AXMinimized` to true via System Events |
| `maximize(h)` | set the front window's position/size to the main screen's visible frame (Finder `bounds of window of desktop` is fine) |
| `wait_for_window(query, timeout)` | poll `find_window` |
| `is_running(name)` | System Events `exists process "<name>"` |
| `monitors()` | best effort: list with the main screen's frame |
| `spotify_playing() -> bool | None` | `None` if Spotify isn't running, else `player state is playing` |
`key_down/key_up/tap` are Windows-only and are not needed on mac (don't define them, and make sure nothing on
the mac path calls them).

`capri/actions.py`: `spotify_playing()` currently parses Windows window titles. Change it to
`return sysctl.spotify_playing()` when that function exists, else the current logic. Also make `spotify_play`
work on mac (if not running: launch via `sysapps`, wait for it, then `media_play_pause()` only if not playing).

## 3. `capri/macapps.py` (new)
- `scan()` lists `*.app` bundles in `/Applications`, `/Applications/Utilities`, `/System/Applications`,
  `/System/Applications/Utilities`, `~/Applications` (not recursive beyond those). `App(name=<bundle stem>,
  app_id=<full path>)`. Cache to `logs/apps.json` exactly like `apps.py`.
- `match()`: same algorithm as `apps.match` (import and reuse it — move it to a shared helper if needed) but with
  mac aliases: chrome→Google Chrome, browser→Safari, vs code/vscode/code→Visual Studio Code, terminal→Terminal,
  settings→System Settings, files/finder→Finder, calculator/calc→Calculator, notes→Notes, music→Music.
- `launch(app)`: `subprocess.Popen(["open", "-a", app.app_id])`.

## 4. Hotkey on mac: `capri/hotkey.py`
Keep the `step()` state machine exactly as is (it's tested). Split key reading into a backend object:
- `class Win32Keys` (current `GetAsyncKeyState` + `keybd_event` mask tap), created lazily only on Windows.
- `class PynputKeys` (mac): a `pynput.keyboard.Listener` + `pynput.mouse.Listener` maintaining a thread-safe set
  of pressed **VK-style codes** using the existing `VK` table: map `Key.alt_l`→0xA4, `Key.alt_r`→0xA5,
  `Key.alt`→0x12, `Key.cmd`/`cmd_l`/`cmd_r`→0x5B, ctrl→0x11 (+0xA2/0xA3 for l/r), shift likewise, `Key.space`→0x20,
  `Key.esc`→0x1B, `Key.f13`→0x7C, letters/digits→`ord(upper)`, any other key→a unique code ≥ 0x100, mouse buttons
  →0x01/0x02/0x04. `down(code)` must treat generic and left/right variants as the same family (use `FAMILY`).
  `tap_mask()` is a no-op on mac.
- `HoldHotkey.__init__(..., keys_backend=None)`: default backend picked by `sys.platform`. `_down`,
  `_other_key_down`, `_tap_mask` delegate to the backend. On mac, `_other_key_down` = any pressed code not in the
  hotkey's own family set.
- Default hotkey `["alt"]` = holding Option alone on mac. If pynput can't read keys (macOS permission missing),
  print once: "Capri can't see the keyboard yet: System Settings → Privacy & Security → Input Monitoring (and
  Accessibility) → enable your Terminal app, then restart Capri." and keep running (wake word still works).

## 5. Voice and speech on mac
- `capri/voice.py`: on non-Windows don't print the "run setup.ps1 / onnxruntime-gpu" warning. Provider choice:
  `[voice] provider = "auto"` (new optional key): on mac `auto` = `CPUExecutionProvider`; allow `"coreml"` →
  `CoreMLExecutionProvider` if `onnxruntime.get_available_providers()` has it, else fall back to CPU with a note.
- `capri/stt.py`: already guards Windows-only CUDA DLL loading; make sure faster-whisper with
  `whisper_device = "cuda"` falls back to CPU on mac without crashing (it may already).
- `model_dir` may be absolute or start with `~` (expand it). On mac, setup puts models in `~/.capri/models`.

## 6. Overlay and hands on mac
- `capri/main.py`: create `Hands()` only on Windows; on mac `self.hands = None`. Create the overlay only on
  Windows (print "overlay: not on macOS yet" once on mac).
- `capri/brain.py`: when `self.tools.hands is None`, leave the hand tools (`tools.HAND_TOOLS`) out of the
  function declarations sent to Gemini, and add one rule line to the prompt: "You can't see or click inside apps
  on this computer yet." (only in that case). Keep the prompt under `PROMPT_CAP` (there's a test).

## 7. Shared memory (both computers)
- New optional config: `[memory] dir = ""`. Empty = today's `ROOT/memory` (unchanged). Otherwise an absolute path
  (expand `~`), e.g. the Google Drive folder next to the life folder, so the PC and the Mac share one memory.
  `main.py` passes it to `Memory(root)`; the write log stays in local `logs/`.

## 8. macOS setup and launcher (new files)
- `setup.sh` (bash, `set -euo pipefail`): find python3.12/3.13/3.11 (`python3.12` … or Homebrew), create the venv
  at `${CAPRI_VENV:-$HOME/.capri/venv}` (outside the folder, because this folder may live in Google Drive),
  `pip install -r requirements.txt`, `pip install --no-deps "silero-vad>=5"`, download the two Kokoro files from
  the same URLs as `setup.ps1` into `$HOME/.capri/models` if missing, write nothing into the repo except
  `config.local.toml` if you need overrides, then run `capri.sh doctor`.
- `capri.sh`: `exec "${CAPRI_VENV:-$HOME/.capri/venv}/bin/python" -u -m capri "$@"` with the same friendly
  messages as `capri.bat` (missing venv → "run ./setup.sh").
- Config: support an optional `config.local.toml` merged over `config.toml` (keys in local win). On mac setup
  writes `[voice] model_dir = "~/.capri/models"` there. Add `config.local.toml` to `.gitignore`.
- `requirements.txt`: add environment markers: `uiautomation`, `winocr`, `nvidia-cublas-cu12`,
  `nvidia-cudnn-cu12` → `; sys_platform == "win32"`; add `pynput>=1.7.6; sys_platform == "darwin"`.

## 9. Doctor on mac: `capri/doctor.py`
OS check accepts Windows **or** macOS. On mac: skip `uiautomation`/`winocr` imports and the CUDA line; check
`pynput` import, `osascript` runs (`osascript -e 'return 1'`), Spotify installed (`/Applications/Spotify.app`),
and print the three macOS permissions to grant (Microphone, Input Monitoring, Accessibility) with where to find
them. Everything else (models, VAD, Claude Code, key, mic, STT, life folder) stays shared.

## 10. Tests (new, must pass on Linux too — mock `subprocess.run` / backends)
- `tests/test_macctl.py`: AppleScript strings for volume up/down/set/mute (clamping), Spotify state parsing
  (`playing`/`paused`/not running → True/False/None), `_q()` escaping (quotes, backslashes), `find_window`
  fuzzy match over a mocked process list.
- `tests/test_macapps.py`: scan over a temp dir of fake `X.app` folders, aliases, launch command.
- `tests/test_hotkey_mac.py`: `PynputKeys`-style fake backend driving `HoldHotkey.step()`: Option hold → press,
  confirm after 0.2 s, release; Option+Tab → cancel; left/right Option both count; Esc callback.
- `tests/test_platform.py`: `sysctl`/`sysapps` pick the mac modules when `sys.platform` is patched to `darwin`
  (reload modules), and Windows modules otherwise; brain leaves out hand tools when `hands=None`.
- `config.local.toml` merge test; `[memory] dir` test.

## 11. Docs
Add a short "Capri on Mac" section to `README.md`: `./setup.sh`, `./capri.sh`, the three permissions, what works
in Phase 1 (everything but seeing/clicking inside apps), hotkey = hold Option, the dashboard URL.

## Done when
`python -m pytest -q` passes (all old + new tests), no Windows code path changed in behavior, and a short
`PHASE1_MAC_NOTES.md` lists what you did, anything you were unsure about, and how you tested.

# Phase 1 macOS Implementation Notes

## What changed

1. **Platform switches (`capri/sysctl.py`, `capri/sysapps.py`)**
   - Added `capri/sysctl.py` (`darwin` → `macctl`, else `winctl`) and `capri/sysapps.py` (`darwin` → `macapps`, else `apps`).
   - Updated `capri/winctl.py` so `ctypes.windll` and `psutil` are guarded at import time, keeping `winctl` importable on Linux/macOS without changing Windows behavior.
   - Updated `capri/actions.py`, `capri/tools.py`, and `capri/main.py` to import `sysctl` and `sysapps` instead of `winctl`/`apps`.
   - Updated `tests/test_core.py` (`test_actions_with_fake_windows`) to also stub `sys.modules["capri.sysctl"]` so the test passes on macOS as well as Windows/Linux.

2. **macOS controls & app index (`capri/macctl.py`, `capri/macapps.py`)**
   - Implemented `capri/macctl.py` using `osascript` via `_osa()` and `_q()` escaping: Spotify media controls (`playpause`, `next track`, `previous track`), volume up/down/set/mute with 0–100 clamping, `spotify_playing()`, `top_windows()`, `find_window()` (via `macapps.match`), `focus()`, `foreground()`, `close()` (AXCloseButton / Cmd+W without quitting), `minimize()` (`AXMinimized`), `maximize()` (desktop bounds), `wait_for_window()`, `is_running()`, and `monitors()`.
   - Updated `capri/actions.py` `spotify_playing()` and `spotify_play()` to use `sysctl.spotify_playing()` when present and avoid toggling playback off if Spotify auto-plays on launch.
   - Implemented `capri/macapps.py` scanning `*.app` bundles in the five standard macOS Applications folders (non-recursive), caching to `logs/apps.json`, reusing `capri.apps.match()` with macOS aliases, and launching via `open -a`.

3. **macOS hotkey (`capri/hotkey.py`)**
   - Split key reading into `Win32Keys` and `PynputKeys` (lazy `pynput` import, thread-safe pressed VK set, `FAMILY` support, mouse button codes, no-op `tap_mask()`, and one-time Input Monitoring / Accessibility permission message on failure).
   - Added `keys_backend=None` to `HoldHotkey.__init__` while keeping `step()` unchanged. On macOS, `Win32Keys` is still selected if `ctypes.windll` is monkeypatched (so existing `tests/test_hotkey.py` continues to pass on macOS without modification).

4. **Voice, STT, Brain, Overlay, Memory**
   - `capri/voice.py`: expanded `~` and absolute paths in `_model_dir()`, suppressed the Windows `setup.ps1` / `onnxruntime-gpu` warning on non-Windows, and added `[voice] provider = "auto"` / `"coreml"` support on macOS.
   - `capri/main.py`: `Hands()` and `Overlay` are only created on Windows (`self.hands = None` on macOS, printing `"overlay: not on macOS yet"` once when enabled); passes `memory_dir(cfg)` to `Memory(...)`.
   - `capri/brain.py`: omits `HAND_TOOLS` from Gemini function declarations and adds `"You can't see or click inside apps on this computer yet."` to the system prompt when `self.tools.hands is None`.
   - `capri/memory.py`: added `memory_dir(cfg)` supporting optional `[memory] dir` (with `~` expansion).

5. **Config, Setup, Doctor, Docs**
   - `capri/config.py`: deep-merges optional `config.local.toml` over `config.toml`; added `config.local.toml` to `.gitignore`.
   - `config.toml`: documented `[voice] provider = "auto"` and `[memory] dir = ""`.
   - `requirements.txt`: added `; sys_platform == "win32"` markers to Windows-only packages and `pynput>=1.7.6; sys_platform == "darwin"`.
   - Created `setup.sh` and `capri.sh` (venv at `~/.capri/venv`, models at `~/.capri/models`, writes `config.local.toml` if missing).
   - `capri/doctor.py`: accepts Windows or macOS, checks `pynput`, `osascript`, `Spotify.app`, expanded `model_dir`, and prints the three macOS permissions.
   - `README.md`: added the "Capri on Mac (Phase 1)" section.

## Anything unsure

- Real macOS `pynput` listener behavior (e.g., `Listener.IS_TRUSTED` and exact key events under macOS Sequoia/Sonoma) and `osascript` window manipulation (`AXCloseButton` / `AXMinimized` / `position` & `size`) were not exercised live since `setup.sh` was not run and macOS permissions/apps were not tested interactively.
- `test_hands.py` requires `pillow` (`PIL`), so `pillow` was installed in `~/.capri/devvenv` alongside `pytest`, `numpy`, and `google-genai`.

## Test results

- Ran `~/.capri/devvenv/bin/python -m pytest -q`: **181 passed in 4.53s** (all 164 existing tests + 17 new tests in `tests/test_macctl.py`, `tests/test_macapps.py`, `tests/test_hotkey_mac.py`, and `tests/test_platform.py`).
- Ran `bash -n setup.sh capri.sh`: exited 0.

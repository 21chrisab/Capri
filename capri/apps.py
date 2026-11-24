"""App index: every Start-menu app (desktop + Store), fuzzy-matched by name.

Built once from PowerShell's Get-StartApps (~1 s) and cached to
logs/apps.json. Launching goes through shell:AppsFolder, which handles
Store apps like Spotify the same as normal .exe apps.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

CACHE = ROOT / "logs" / "apps.json"
NOISE = re.compile(r"\b(?:the|app|application|desktop|for windows|microsoft|google)\b")

# What people say -> words that appear in the Start-menu name.
ALIASES = {
    "chrome": "google chrome", "browser": "google chrome", "edge": "microsoft edge",
    "calculator": "calculator", "calc": "calculator", "notepad": "notepad",
    "file explorer": "file explorer", "files": "file explorer", "explorer": "file explorer",
    "settings": "settings", "terminal": "terminal", "vs code": "visual studio code", "vscode": "visual studio code",
    "code": "visual studio code", "word": "word", "excel": "excel", "task manager": "task manager",
}


@dataclass(frozen=True)
class App:
    name: str
    app_id: str


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", NOISE.sub(" ", s.lower())).strip()


def scan() -> list[App]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    rows = json.loads(out.stdout or "[]")
    rows = [rows] if isinstance(rows, dict) else rows
    apps = [App(r["Name"], r["AppID"]) for r in rows if r.get("Name") and r.get("AppID")]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps([a.__dict__ for a in apps]), encoding="utf-8")
    return apps


def load(refresh: bool = False) -> list[App]:
    if not refresh and CACHE.exists():
        return [App(**r) for r in json.loads(CACHE.read_text(encoding="utf-8"))]
    return scan()


def match(query: str, apps: list[App], aliases: dict[str, str] = ALIASES) -> App | None:
    """Exact > alias > prefix/substring > fuzzy (cutoff 0.75). Pure, testable."""
    q = _clean(aliases.get(query.lower().strip(), query))
    if not q:
        return None
    names = {a: _clean(a.name) for a in apps}
    for a, n in names.items():
        if n == q:
            return a
    hits = [a for a, n in names.items() if n.startswith(q) or re.search(rf"\b{re.escape(q)}\b", n)]
    if hits:
        return min(hits, key=lambda a: len(a.name))  # "Spotify" over "Spotify Widget Helper"
    close = difflib.get_close_matches(q, list(names.values()), n=1, cutoff=0.75)
    if close:
        return next(a for a, n in names.items() if n == close[0])
    return sounds_like(q, apps)


def _skeleton(word: str) -> str:
    """A rough sound key: c/k/q/ck -> k, ph -> f, drop h/w/y and vowels after the first letter.
    "quad" and "claude" both become k-d-ish, which is how speech-to-text mishears them."""
    w = re.sub(r"[^a-z]", "", word.lower())
    w = w.replace("ph", "f").replace("ck", "k").replace("qu", "k")
    w = re.sub(r"[cq]", "k", w)
    if not w:
        return ""
    return w[0] + re.sub(r"[aeiouhwy]", "", w[1:])


def sounds_like(query: str, apps: list[App]) -> App | None:
    """Last resort for a misheard single-word app name ("quad" -> Claude). Only a clear, unique winner counts."""
    q = query.strip()
    if " " in q or len(q) < 3:
        return None
    key = _skeleton(q)
    scored = []
    for a in apps:
        name = _clean(a.name)
        if not name or " " in name:
            continue
        ratio = difflib.SequenceMatcher(None, key, _skeleton(name)).ratio()
        scored.append((ratio, a))
    scored.sort(key=lambda r: r[0], reverse=True)
    if scored and scored[0][0] >= 0.8 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
        return scored[0][1]
    return None


def launch(app: App) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.app_id}"],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class AppIndex:
    def __init__(self) -> None:
        self.apps = load()

    def find(self, query: str) -> App | None:
        hit = match(query, self.apps)
        if hit is None:  # maybe something was installed since the cache was built
            self.apps = load(refresh=True)
            hit = match(query, self.apps)
        return hit

    @staticmethod
    def cache_path() -> Path:
        return CACHE

"""macOS app index: *.app bundles in standard Applications folders, fuzzy-matched by name."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .apps import CACHE, App, match as _match

APP_DIRS = [
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
]

ALIASES = {
    "chrome": "google chrome",
    "browser": "safari",
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code": "visual studio code",
    "terminal": "terminal",
    "settings": "system settings",
    "files": "finder",
    "finder": "finder",
    "calculator": "calculator",
    "calc": "calculator",
    "notes": "notes",
    "music": "music",
}


def scan(dirs: list[Path] | None = None, cache: Path = CACHE) -> list[App]:
    """List *.app bundles in the standard macOS Applications directories (non-recursive)."""
    apps: list[App] = []
    seen: set[str] = set()
    for d in (dirs if dirs is not None else APP_DIRS):
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if entry.suffix == ".app" and entry.name not in seen:
                seen.add(entry.name)
                apps.append(App(name=entry.stem, app_id=str(entry)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([a.__dict__ for a in apps]), encoding="utf-8")
    return apps


def load(refresh: bool = False) -> list[App]:
    if not refresh and CACHE.exists():
        return [App(**r) for r in json.loads(CACHE.read_text(encoding="utf-8"))]
    return scan()


def match(query: str, apps: list[App]) -> App | None:
    """Exact > alias > prefix/substring > fuzzy (cutoff 0.75)."""
    return _match(query, apps, aliases=ALIASES)


def launch(app: App) -> None:
    subprocess.Popen(["open", "-a", app.app_id])


class AppIndex:
    def __init__(self) -> None:
        self.apps = load()

    def find(self, query: str) -> App | None:
        hit = match(query, self.apps)
        if hit is None:
            self.apps = load(refresh=True)
            hit = match(query, self.apps)
        return hit

    @staticmethod
    def cache_path() -> Path:
        return CACHE

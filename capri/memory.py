"""Capri's memory: a folder of Markdown files.

memory/
  SOUL.md            her personality. Always in the prompt. Only you edit it.
  USER.md            about you. Always in the prompt. She can add to it.
  people/*.md        one file per person
  topics/*.md        tastes, routines, setup (music.md, games.md, pc.md ...)
  projects/*.md      ongoing stuff
  (any other folder/file you like; it just shows up in the index)
  daily/YYYY-MM-DD.md   automatic log of the day. Not indexed, read on request.

Every turn the prompt gets SOUL.md + USER.md + an INDEX: one line per other
file ("topics/music.md: likes lo-fi..."). She opens a file with recall()
only when it matters, so memory can grow without slowing her down.
Writes only come from her remember/forget tools, and every write is logged.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .config import ROOT

MEM = ROOT / "memory"
WRITE_LOG = ROOT / "logs" / "memory_writes.jsonl"


def memory_dir(cfg: dict) -> Path:
    """Return configured [memory] dir (expanded) or default ROOT/memory."""
    raw = cfg.get("memory", {}).get("dir", "").strip()
    if not raw:
        return MEM
    return Path(raw).expanduser()
PINNED = ("SOUL.md", "USER.md")
READ_ONLY = {"SOUL.md"}
SAFE = re.compile(r"^[a-z0-9][a-z0-9 _\-/]*\.md$")

DEFAULT_SOUL = """# Capri
You're Capri, the user's voice assistant on their Windows PC. You talk like a chill friend:
casual, a little slang, short. Never robotic, never "Certainly! I'd be happy to help."
- Spoken out loud: no lists, no markdown, no emojis. One or two sentences unless asked for more.
- For tasks, don't narrate. An ack plays automatically; only speak if something went wrong or there's an answer to give.
- If you're not sure what they meant, ask one quick question.
"""

DEFAULT_USER = """# User
- Uses a Windows PC with two monitors and a fifine AM6 mic.
- Plays music through the Spotify desktop app.
"""


class Memory:
    def __init__(self, root: Path = MEM, write_log: Path = WRITE_LOG) -> None:
        self.root = root
        self.write_log = write_log
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in ("people", "topics", "projects", "daily"):
            (self.root / sub).mkdir(exist_ok=True)
        if not (self.root / "SOUL.md").exists():
            (self.root / "SOUL.md").write_text(DEFAULT_SOUL, encoding="utf-8")
        if not (self.root / "USER.md").exists():
            (self.root / "USER.md").write_text(DEFAULT_USER, encoding="utf-8")

    # ---------- paths ----------
    def _path(self, rel: str) -> Path:
        rel = rel.strip().lower().replace("\\", "/")
        if not rel.endswith(".md"):
            rel += ".md"
        if not SAFE.match(rel) or ".." in rel:
            raise ValueError(f"bad memory path: {rel!r}")
        if rel.upper() in {p.upper() for p in PINNED}:
            rel = rel.upper().replace(".MD", ".md")  # soul.md -> SOUL.md
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"outside memory: {rel!r}")
        return p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    # ---------- prompt ----------
    def pinned(self) -> str:
        return "\n\n".join((self.root / n).read_text(encoding="utf-8").strip()
                           for n in PINNED if (self.root / n).exists())

    def files(self) -> list[Path]:
        out = [p for p in self.root.rglob("*.md")
               if p.name not in PINNED or p.parent != self.root]
        out = [p for p in out if "daily" not in p.relative_to(self.root).parts]
        return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)

    @staticmethod
    def _summary(p: Path, width: int = 90) -> str:
        lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        body = [l.lstrip("-* ").strip() for l in lines if not l.startswith("#")]
        s = "; ".join(body)
        return (s[: width - 1] + "…") if len(s) > width else s

    def index(self, max_chars: int = 2000) -> str:
        """One line per file, most recently changed first, cut to fit max_chars."""
        lines, used = [], 0
        for p in self.files():
            line = f"- {self._rel(p)}: {self._summary(p)}"
            if used + len(line) + 1 > max_chars:
                lines.append(f"- (+{len(self.files()) - len(lines)} older files; use recall to open by path)")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines) or "- (no memory files yet)"

    # ---------- tools ----------
    def recall(self, path: str) -> str:
        p = self._path(path)
        if not p.exists():
            near = [self._rel(f) for f in self.files() if Path(path).stem.lower() in f.stem.lower()]
            return f"No file {self._rel(p)}." + (f" Similar: {', '.join(near[:5])}" if near else "")
        return p.read_text(encoding="utf-8")[:4000]

    def remember(self, path: str, fact: str) -> str:
        p = self._path(path)
        if p.name in READ_ONLY and p.parent == self.root:
            return "SOUL.md is edited by the user only."
        fact = " ".join(fact.split()).strip()
        if not fact:
            return "Nothing to save."
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(f"# {p.stem.replace('-', ' ').replace('_', ' ').title()}\n", encoding="utf-8")
        text = p.read_text(encoding="utf-8")
        if fact.lower() in text.lower():
            return f"Already in {self._rel(p)}."
        with open(p, "a", encoding="utf-8") as f:
            f.write(("" if text.endswith("\n") else "\n") + f"- {fact}\n")
        self._log("remember", p, fact)
        return f"Saved to {self._rel(p)}."

    def forget(self, path: str, text: str) -> str:
        p = self._path(path)
        if p.name in READ_ONLY and p.parent == self.root:
            return "SOUL.md is edited by the user only."
        if not p.exists():
            return f"No file {self._rel(p)}."
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        needle = text.lower().strip()
        keep = [l for l in lines if l.startswith("#") or needle not in l.lower()]
        removed = len(lines) - len(keep)
        if removed:
            p.write_text("".join(keep), encoding="utf-8")
            self._log("forget", p, text)
        return f"Removed {removed} line(s) from {self._rel(p)}."

    def log_day(self, user: str, capri: str) -> None:
        now = dt.datetime.now()
        p = self.root / "daily" / f"{now:%Y-%m-%d}.md"
        if not p.exists():
            p.write_text(f"# {now:%A %B %d, %Y}\n", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"- {now:%H:%M} you: {user} → {capri}\n")

    def _log(self, op: str, p: Path, text: str) -> None:
        self.write_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.write_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                                "op": op, "file": self._rel(p), "text": text}) + "\n")

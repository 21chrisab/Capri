"""Life OS: Capri's view of the user's life/ folder (tasks, projects, inbox).

The folder lives in Google Drive so Claude Code, the dashboard, Obsidian and the phone share it.
Capri reads it and makes small, safe writes:
  capture(text)      append a line to inbox.md
  today_summary()    the Top 3 from today.md, said out loud
  done(text)         check off a matching open task in today.md and projects/*.md
  add_task(...)      append a task to a project's ## Tasks
  dashboard()        rebuild dashboard.html (life/dashboard/build.py) and open it
Formats are documented in life/CLAUDE.md. Everything else (planning, reviews, memory,
opportunities) is done by Claude Code skills in that folder.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

TASK = re.compile(r"^(\s*[-*]\s+\[)( |x|X)(\]\s+)(.*)$")
DUE = re.compile(r"\bdue:(\d{4}-\d{2}-\d{2})\b")
STOP = {"the", "a", "an", "my", "to", "for", "on", "of", "and", "task", "thing", "that", "this", "with", "in"}


def candidates() -> list[Path]:
    """Where Google Drive for desktop usually puts 'My Drive' on Windows and macOS."""
    home = Path.home()
    out = [Path(f"{d}:/My Drive/life") for d in "GHIJ"]
    out += [home / "My Drive" / "life", home / "Google Drive" / "My Drive" / "life", home / "Google Drive" / "life"]
    cloud = home / "Library" / "CloudStorage"
    if cloud.is_dir():
        out += [p / "My Drive" / "life" for p in cloud.glob("GoogleDrive-*")]
    return out


def find(cfg: dict) -> "Life | None":
    """[life] dir from config.toml, else the usual Google Drive spots. None = not set up."""
    configured = str(cfg.get("life", {}).get("dir", "")).strip()
    for p in ([Path(os.path.expandvars(configured)).expanduser()] if configured else candidates()):
        try:
            if (p / "projects").is_dir() and (p / "today.md").exists():
                return Life(p)
        except OSError:
            continue
    return None


def _speakable(text: str) -> str:
    text = DUE.sub("", text)
    text = re.sub(r"\s*\([^)]*\)", "", text)        # drop parentheticals
    text = re.sub(r"[`*_]", "", text)
    return " ".join(text.split()).strip(" ,.;:")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1}


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


class Life:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dashboard_url: str | None = None     # set when Capri's web server is running

    # ---------- files ----------
    def _read(self, rel: str) -> str:
        try:
            return (self.root / rel).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _write(self, p: Path, text: str) -> None:
        p.write_text(text, encoding="utf-8", newline="")

    def projects(self) -> list[Path]:
        return sorted((self.root / "projects").glob("*.md"))

    @staticmethod
    def meta(text: str) -> dict[str, str]:
        if not text.startswith("---"):
            return {}
        end = text.find("\n---", 3)
        out = {}
        for line in text[3:end if end != -1 else 0].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip().lower()] = v.split(" #", 1)[0].strip()
        return out

    @staticmethod
    def _section(text: str, heading: str) -> list[str]:
        out, inside = [], False
        for line in text.splitlines():
            if line.startswith("## "):
                inside = line[3:].strip().lower() == heading.lower()
            elif inside:
                out.append(line)
        return out

    def find_project(self, name: str) -> Path | None:
        want = _words(name.replace("-", " "))
        best, score = None, 0
        for p in self.projects():
            text = self._read(f"projects/{p.name}")
            title = next((l[2:] for l in text.splitlines() if l.startswith("# ")), p.stem)
            s = len(want & (_words(p.stem.replace("-", " ")) | _words(title)))
            if s > score:
                best, score = p, s
        return best

    # ---------- reads ----------
    def top3(self) -> tuple[str, list[tuple[bool, str]]]:
        text = self._read("today.md")
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), "")
        items = []
        for line in self._section(text, "Top 3"):
            m = TASK.match(line)
            if m:
                items.append((m.group(2).lower() == "x", m.group(4).strip()))
        return title, items

    def due_soon(self, days: int = 3, today: dt.date | None = None) -> list[str]:
        today = today or dt.date.today()
        out = []
        for line in self._section(self._read("canvas.md"), "Upcoming"):     # Canvas assignments (synced)
            m, d = TASK.match(line), DUE.search(line)
            if m and m.group(2) == " " and d:
                try:
                    if 0 <= (dt.date.fromisoformat(d.group(1)) - today).days <= days:
                        out.append(m.group(4))
                except ValueError:
                    pass
        for p in self.projects():
            text = self._read(f"projects/{p.name}")
            if self.meta(text).get("status", "active") != "active":
                continue
            for line in self._section(text, "Tasks"):
                m, d = TASK.match(line), DUE.search(line)
                if m and m.group(2) == " " and d:
                    try:
                        if (dt.date.fromisoformat(d.group(1)) - today).days <= days:
                            out.append(m.group(4))
                    except ValueError:
                        pass
        return out

    def today_summary(self, today: dt.date | None = None) -> str:
        """One or two spoken sentences."""
        today = today or dt.date.today()
        title, items = self.top3()
        if not items:
            return "Nothing's planned for today yet."
        left = [_speakable(t) for done, t in items if not done]
        if not left:
            out = "You knocked out your whole top three."
        elif len(left) == 1:
            out = f"One left on your top three: {left[0]}."
        else:
            out = f"Top {'three' if len(left) == 3 else len(left)} today: {_join(left)}."
        m = re.search(r"\d{4}-\d{2}-\d{2}", title)
        if m and m.group() != today.isoformat():
            out += f" Heads up, that list is from {dt.date.fromisoformat(m.group()):%A}."
        soon = self.due_soon(today=today)
        if soon:
            out += f" Also, {len(soon)} thing{'s' if len(soon) > 1 else ''} due in the next few days."
        return out

    def today_text(self) -> str:
        text = self._read("today.md")[:3000] or "today.md is empty"
        canvas = self._read("canvas.md")
        return text + ("\n\n" + canvas[:1500] if canvas else "")

    def projects_text(self) -> str:
        lines = []
        for p in self.projects():
            text = self._read(f"projects/{p.name}")
            m = self.meta(text)
            n_open = sum(1 for l in self._section(text, "Tasks") if (t := TASK.match(l)) and t.group(2) == " ")
            lines.append(f"- {p.stem} [{m.get('status', 'active')}, {m.get('area', '')}] next: "
                         f"{m.get('next', '(none)')} ({n_open} open tasks)")
        return "\n".join(lines) or "no projects yet"

    def project_text(self, name: str) -> str:
        p = self.find_project(name)
        if p is None:
            return f"no project matches '{name}'. Projects: {', '.join(x.stem for x in self.projects())}"
        return self._read(f"projects/{p.name}")[:3000]

    # ---------- writes ----------
    def capture(self, text: str) -> str:
        text = " ".join(text.split()).strip(" .")
        if not text:
            return "Nothing to add."
        p = self.root / "inbox.md"
        cur = self._read("inbox.md") or "# Inbox\n"
        sep = "" if cur.endswith("\n") else "\n"
        self._write(p, cur + sep + f"- {text}\n")
        return f"Added to your inbox: {text}"

    def add_task(self, text: str, project: str = "", due: str = "") -> str:
        text = " ".join(text.split()).strip(" .")
        if not project:
            return self.capture(text + (f" due:{due}" if due else ""))
        p = self.find_project(project)
        if p is None:
            return self.capture(text + (f" due:{due}" if due else "")) + f" (no project matched '{project}')"
        line = f"- [ ] {text}" + (f" due:{due}" if due else "")
        lines = self._read(f"projects/{p.name}").splitlines()
        try:
            start = next(i for i, l in enumerate(lines) if l.strip().lower() == "## tasks")
        except StopIteration:
            lines += ["", "## Tasks", line]
        else:
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            last = max((i for i in range(start + 1, end) if lines[i].strip()), default=start)
            lines.insert(last + 1, line)
        self._write(p, "\n".join(lines) + "\n")
        return f"Added to {p.stem}: {text}"

    def done(self, query: str) -> str:
        """Check off the open task that matches every important word in query."""
        want = _words(query)
        if not want:
            return "Which task?"
        files = [self.root / "today.md", *self.projects()]
        hits: dict[str, list[tuple[Path, int]]] = {}
        for p in files:
            for i, line in enumerate(self._read(p.relative_to(self.root).as_posix()).splitlines()):
                m = TASK.match(line)
                if m and m.group(2) == " " and want <= _words(m.group(4)):
                    hits.setdefault(_speakable(m.group(4)).lower(), []).append((p, i))
        if not hits:
            return f"Couldn't find an open task matching '{query}'."
        if len(hits) > 1:
            return "Which one: " + " or ".join(sorted(hits)[:3]) + "?"
        (name, spots), = hits.items()
        for p in {p for p, _ in spots}:
            lines = self._read(p.relative_to(self.root).as_posix()).splitlines()
            for _, i in (s for s in spots if s[0] == p):
                lines[i] = TASK.sub(r"\1x\3\4", lines[i])
            self._write(p, "\n".join(lines) + "\n")
        return f"Checked off: {name}"

    def dashboard(self, open_it: bool = True) -> str:
        script = self.root / "dashboard" / "build.py"
        if not script.exists():
            return "dashboard/build.py is missing from the life folder"
        r = subprocess.run([sys.executable, str(script)], cwd=self.root, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return f"dashboard build failed: {(r.stderr or r.stdout).strip()[-300:]}"
        html = self.root / "dashboard" / "dashboard.html"
        if open_it and self.dashboard_url:
            import webbrowser
            webbrowser.open(self.dashboard_url)
        elif open_it:
            if sys.platform == "win32":
                os.startfile(html)  # noqa: S606 - opens in the default browser
            else:
                import webbrowser
                webbrowser.open(html.as_uri())
        return "opened the dashboard"

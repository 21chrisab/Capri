"""Handoff: Capri passes big jobs to Claude (Claude Code, headless) and keeps talking to you.

  "Capri, have Claude research the best budget far-field mics and write me a comparison."
   -> Capri: "Handing that to Claude. I'll tell you when it's done."
   -> runs `claude -p` in the background, in its own folder: handoffs/<date>-<slug>/
   -> when it finishes, Capri says Claude's 1-2 sentence summary out loud.
      The full answer is in RESULT.md ("Capri, open Claude's result").

Runs on your Claude Pro plan through the Claude Code CLI you're logged into. No API key.
Safety: Claude works inside the job folder only (acceptEdits mode = it can create and edit
files there, nothing else), can search and read the web, and can't run shell commands.

Codex (ChatGPT plan) and Gemini CLI (Google AI plan) can take jobs too ("have Codex ..."), held to
the same idea: edits stay in the job folder, no free shell. Codex runs in its workspace-write sandbox
(network off, writes only in the job folder). Gemini runs in auto_edit mode, where shell commands need
approval, and headless runs deny anything that needs approval.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import ROOT

JOBS = ROOT / "handoffs"
RUN_PROMPT = "Do the task in TASK.md in this folder. Follow its instructions exactly."

SYSTEM = """You were handed this task by Capri, the user's voice assistant, because it's too big to do by voice.
The user is not watching. Don't ask questions: make sensible assumptions and note them.
Put your complete answer in RESULT.md in the current folder (markdown, well organized; files you create go here too).
Your FINAL message must be only a spoken summary for Capri to read aloud: 1-2 short casual sentences, plain text,
no markdown, no file paths, no lists."""

AGENTS = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:n].rstrip("-")) or "task"


def find_claude() -> str | None:
    """claude.exe from the native installer, or claude/claude.cmd from npm."""
    exe = shutil.which("claude")
    if exe:
        return exe
    for p in (Path.home() / ".local" / "bin" / "claude.exe",
              Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if p.exists():
            return str(p)
    return None


def build_command(exe: str, max_turns: int, model: str | None = None) -> list[str]:
    cmd = [exe, "-p", RUN_PROMPT,
           "--output-format", "json",
           "--permission-mode", "acceptEdits",          # edits allowed only inside the job folder
           "--allowedTools", "WebSearch,WebFetch",      # web research without prompts
           "--disallowedTools", "Bash,PowerShell",      # no shell commands
           "--max-turns", str(max_turns),
           "--append-system-prompt", SYSTEM]
    if model:
        cmd += ["--model", model]
    return cmd


def find_agent(agent: str) -> str | None:
    if agent == "claude":
        return find_claude()
    exe = shutil.which(agent)
    if exe:
        return exe
    p = Path(os.environ.get("APPDATA", "")) / "npm" / f"{agent}.cmd"
    return str(p) if p.exists() else None


def build_agent_command(agent: str, exe: str, max_turns: int, model: str | None = None) -> list[str]:
    if agent == "codex":
        cmd = [exe, "exec", "--sandbox", "workspace-write",   # writes only in the job folder, network off
               "--skip-git-repo-check", RUN_PROMPT]           # job folders aren't git repos
    elif agent == "gemini":
        cmd = [exe, "-p", RUN_PROMPT,
               "--approval-mode", "auto_edit"]                # edits ok; shell needs approval = denied headless
    else:
        return build_command(exe, max_turns, model)
    return cmd + (["-m", model] if model else [])


def last_paragraph(stdout: str, n: int = 300) -> str:
    """Codex and Gemini print their final message last; that's the spoken summary."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", stdout.strip()) if p.strip()]
    return " ".join(paras[-1].split())[:n] if paras else ""


def parse_output(stdout: str) -> dict:
    """claude -p --output-format json -> {ok, summary, session_id, cost, turns}. Tolerates junk before the JSON."""
    text = stdout.strip()
    start = text.find("{")
    try:
        data = json.loads(text[start:]) if start >= 0 else {}
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, list):                            # some versions emit a list of events
        data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), {})
    summary = str(data.get("result") or "").strip()
    return {
        "ok": bool(data) and not data.get("is_error", False),
        "summary": " ".join(summary.split())[:500],
        "session_id": data.get("session_id", ""),
        "cost": data.get("total_cost_usd"),
        "turns": data.get("num_turns"),
    }


def explain_error(msg: str) -> str:
    """Turn Claude Code's error text into something worth saying out loud."""
    low = msg.lower()
    if "authentication" in low or "log in" in low or "login" in low or "/login" in low:
        return "Claude Code isn't logged in. Open a terminal, run claude, and log in with your Claude account."
    if "usage limit" in low or "rate limit" in low or "limit reached" in low:
        return "Your Claude plan hit its usage limit. Try again later."
    if "max turns" in low or "max_turns" in low:
        return "Claude ran out of steps before finishing. What it got done is in the job folder."
    return "Claude hit a problem: " + (msg[:200] or "unknown error")


@dataclass
class Job:
    id: int
    title: str
    task: str
    folder: Path
    agent: str = "claude"
    status: str = "running"           # running / done / failed
    started: float = field(default_factory=time.time)
    finished: float | None = None
    summary: str = ""
    session_id: str = ""

    @property
    def result_path(self) -> Path:
        return self.folder / "RESULT.md"

    def age(self) -> str:
        secs = int((self.finished or time.time()) - self.started)
        return f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"


class Handoff:
    def __init__(self, cfg: dict, on_done: Callable[[Job], None] | None = None,
                 runner: Callable[..., subprocess.CompletedProcess] | None = None,
                 jobs_dir: Path = JOBS, memory=None) -> None:
        c = cfg.get("handoff", {})
        self.default_agent = str(c.get("default_agent", "claude")).lower() or "claude"
        self.models = {"claude": c.get("model") or None, "codex": c.get("codex_model") or None,
                       "gemini": c.get("gemini_model") or None}
        self.memory = memory
        self.max_turns = int(c.get("max_turns", 40))
        self.timeout = int(c.get("timeout_min", 30)) * 60
        self.model = c.get("model") or None
        self.max_parallel = int(c.get("max_parallel", 2))
        self.on_done = on_done
        self.runner = runner or subprocess.run
        self.jobs_dir = jobs_dir
        self.jobs: list[Job] = []
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.runner is not subprocess.run or any(find_agent(a) for a in AGENTS)

    # ---------- tools Capri calls ----------
    def start(self, task: str, title: str | None = None, agent: str | None = None) -> str:
        agent = (agent or self.default_agent).strip().lower()
        if agent not in AGENTS:
            return f"Unknown agent {agent!r}. Use claude, codex or gemini."
        name = AGENTS[agent]
        exe = find_agent(agent) if self.runner is subprocess.run else agent
        if exe is None:
            if agent == "claude":
                return ("Claude Code isn't installed. Tell the user to run this in PowerShell: "
                        "irm https://claude.ai/install.ps1 | iex   then run `claude` once to log in.")
            pkg = {"codex": "@openai/codex", "gemini": "@google/gemini-cli"}[agent]
            return f"{name} CLI isn't installed. Tell the user to run: npm i -g {pkg}   then run `{agent}` once to log in."
        running = [j for j in self.jobs if j.status == "running"]
        if len(running) >= self.max_parallel:
            return f"Agents are already busy with {len(running)} jobs: {', '.join(j.title for j in running)}."
        title = (title or task).strip().split("\n")[0][:60]
        folder = self.jobs_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{slug(title)}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "TASK.md").write_text(self._task_md(agent, title, task), encoding="utf-8")
        with self._lock:
            job = Job(id=len(self.jobs) + 1, title=title, task=task, folder=folder, agent=agent)
            self.jobs.append(job)
        threading.Thread(target=self._run, args=(job, exe), name=f"handoff-{job.id}", daemon=True).start()
        return f"Started {name} job #{job.id}: {title}. It runs in the background; you'll hear when it's done."

    def _task_md(self, agent: str, title: str, task: str) -> str:
        parts = [f"# {title}\n\n{task.strip()}\n"]
        if self.memory is not None:
            mode, rules = self.memory.active_mode()
            if mode != "general" and rules:
                parts.append(f"## Workflow mode: {mode}\n{rules}\n")
            bullets = [l for l in self.memory.recent_lessons().splitlines() if l.startswith("- ")]
            if bullets:
                parts.append("## Lessons from the user (follow these)\n" + "\n".join(bullets) + "\n")
        if agent != "claude":            # Claude gets these rules via --append-system-prompt
            parts.append("## How to finish\n" + SYSTEM + "\n")
        return "\n".join(parts)

    def status(self) -> str:
        if not self.jobs:
            return "No agent jobs yet."
        return "\n".join(f"#{j.id} {j.title}" + ("" if j.agent == "claude" else f" ({AGENTS[j.agent]})") + f": {j.status} ({j.age()})" + (f". {j.summary}" if j.summary else "")
                         for j in self.jobs[-5:])

    def result(self, which: str = "latest") -> str:
        job = self._pick(which)
        if job is None:
            return "No finished agent job found."
        if job.result_path.exists():
            return f"#{job.id} {job.title}:\n" + job.result_path.read_text(encoding="utf-8")[:3500]
        return f"#{job.id} {job.title}: {job.summary or 'no RESULT.md written'}"

    def open(self, which: str = "latest") -> str:
        job = self._pick(which)
        if job is None:
            return "No finished agent job found."
        target = job.result_path if job.result_path.exists() else job.folder
        os.startfile(str(target))  # type: ignore[attr-defined]  # Windows: opens in the default app
        return f"Opened {target.name} for job #{job.id}."

    # ---------- internals ----------
    def _pick(self, which: str) -> Job | None:
        done = [j for j in self.jobs if j.status != "running"]
        if not done:
            return None
        m = re.search(r"\d+", str(which))
        if m:
            return next((j for j in self.jobs if j.id == int(m.group())), None)
        return done[-1]

    def _run(self, job: Job, exe: str) -> None:
        name = AGENTS[job.agent]
        cmd = build_agent_command(job.agent, exe, self.max_turns, self.models.get(job.agent))
        try:
            proc = self.runner(cmd, cwd=str(job.folder), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=self.timeout, stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out_name = "claude-output.json" if job.agent == "claude" else f"{job.agent}-output.txt"
            (job.folder / out_name).write_text(proc.stdout or "", encoding="utf-8")
            if proc.stderr:
                (job.folder / f"{job.agent}-stderr.txt").write_text(proc.stderr, encoding="utf-8")
            if job.agent == "claude":
                out = parse_output(proc.stdout or "")
                job.session_id = out["session_id"]
                ok, summary = out["ok"], out["summary"]
            else:
                ok, summary = proc.returncode == 0, last_paragraph(proc.stdout or "")
            if ok:
                job.status = "done"
                job.summary = summary or ("Finished. The details are in RESULT.md." if job.result_path.exists()
                                          else "Finished, but it didn't write a RESULT.md.")
            else:
                job.status = "failed"
                err = summary or (proc.stderr or "").strip()[-200:]
                job.summary = explain_error(err) if job.agent == "claude" else f"{name} hit a problem: {err[:200] or 'unknown error'}"
        except subprocess.TimeoutExpired:
            job.status, job.summary = "failed", f"{name} ran past the {self.timeout // 60} minute limit."
        except Exception as e:
            job.status, job.summary = "failed", f"Couldn't run {name}: {type(e).__name__}: {e}"
        job.finished = time.time()
        if self.on_done:
            try:
                self.on_done(job)
            except Exception:
                pass

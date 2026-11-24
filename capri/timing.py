"""Per-turn stage timers and the latency budget report.

Each turn writes one JSON line to logs/timings.jsonl with its kind
(echo, instant, brain, ...) and milliseconds since t0 (the moment the user
stopped talking) for every stage reached. Budgets are set per kind.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import ROOT

LOG = ROOT / "logs" / "timings.jsonl"


class TurnTimer:
    def __init__(self, kind: str = "turn", log: Path = LOG) -> None:
        self.kind = kind
        self.log = log
        self.t0 = time.perf_counter()
        self.marks: dict[str, float] = {}
        self.extra: dict = {}

    def mark(self, stage: str) -> float:
        ms = (time.perf_counter() - self.t0) * 1000
        self.marks.setdefault(stage, round(ms, 1))
        return ms

    def save(self) -> dict:
        row = {"ts": time.time(), "kind": self.kind, **self.marks, **self.extra}
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return row


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def report(budgets: dict[str, dict[str, float]], log: Path = LOG, last: int = 50) -> tuple[str, bool]:
    """budgets = {kind: {stage: ms}}. Returns (text, ok); ok is False if any budgeted p50 is over."""
    if not log.exists():
        return "No turns logged yet. Talk to Capri first.", True
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()][-last:]
    ok = True
    lines = [f"Last {len(rows)} turns, ms since you stopped talking"]
    for kind in sorted({r.get("kind", "turn") for r in rows}):
        krows = [r for r in rows if r.get("kind", "turn") == kind]
        kb = budgets.get(kind, {})
        lines += ["", f"[{kind}]  {len(krows)} turns", f"  {'stage':<16}{'p50':>8}{'p95':>8}{'budget':>9}  status"]
        stages = sorted({k for r in krows for k, v in r.items() if isinstance(v, (int, float)) and k not in ("ts", "audio_s")})
        for st in stages:
            vals = [r[st] for r in krows if isinstance(r.get(st), (int, float))]
            p50, p95 = pct(vals, 0.5), pct(vals, 0.95)
            b = kb.get(st)
            status = "" if b is None else ("OK" if p50 <= b else "OVER")
            if b is not None:
                ok &= p50 <= b
            lines.append(f"  {st:<16}{p50:>8.0f}{p95:>8.0f}{(str(int(b)) if b is not None else '-'):>9}  {status}")
    return "\n".join(lines), ok

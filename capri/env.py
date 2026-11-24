"""Loads .env from the project root into os.environ (existing variables win). No dependency."""
from __future__ import annotations

import os
from pathlib import Path

from .config import ROOT


def load_env(path: Path | None = None) -> None:
    if path is None:                   # ~/.capri/.env (Mac: keeps the key out of Google Drive), then ./.env
        for p in (Path.home() / ".capri" / ".env", ROOT / ".env"):
            load_env(p)
        return
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        else:
            val = val.split(" #", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = val

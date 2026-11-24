"""Loads config.toml from the project root, with optional config.local.toml overrides."""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: Path | None = None) -> dict:
    path = path or ROOT / "config.toml"
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    local = path.with_name("config.local.toml")
    if local.exists():
        with open(local, "rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    return cfg

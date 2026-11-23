"""Small helpers to read inputs and save results safely (saved after every app, so a crash loses nothing)."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from . import config


def load_apps(only: list[str] | None = None, limit: int | None = None) -> list[dict]:
    with open(config.APPS_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    if only:
        wanted = {o.strip().lower() for o in only}
        rows = [r for r in rows if r["app"].lower() in wanted]
    if limit:
        rows = rows[:limit]
    return rows


def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text())
    return {} if default is None else default


def save_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    os.replace(tmp, path)

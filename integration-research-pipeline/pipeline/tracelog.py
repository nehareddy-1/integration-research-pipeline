"""Per-app trace logs: <workspace>/logs/<app-slug>.jsonl, one line per step.

These power the 'click an app to see what the agent did' view, and every line is also sent
to the live log on the run screen.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import config, progress


def slug(app: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", app.lower()).strip("-")


def log(app: str, stage: str, step: str, inp=None, out=None, **extra):
    line = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": app,
        "stage": stage,
        "step": step,
        "input": inp,
        "output": out,
        **extra,
    }
    with open(config.LOGS / f"{slug(app)}.jsonl", "a") as f:
        f.write(json.dumps(line, default=str) + "\n")
    msg = progress.describe(step, inp, out)
    if msg:
        progress.emit({"type": "log", "stage": stage, "app": app, "msg": msg, "ts": line["ts"]})


def read(app: str) -> list[dict]:
    p = config.LOGS / f"{slug(app)}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def clear(app: str, stage: str | None = None):
    """Remove an app's log lines (all, or just one stage) before a re-run."""
    p = config.LOGS / f"{slug(app)}.jsonl"
    if not p.exists():
        return
    if stage is None:
        p.unlink()
        return
    keep = [l for l in read(app) if l["stage"] != stage]
    p.write_text("".join(json.dumps(l) + "\n" for l in keep))

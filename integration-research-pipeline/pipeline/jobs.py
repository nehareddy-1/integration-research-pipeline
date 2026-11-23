"""Web-app runs: parse an uploaded CSV, run every stage in the background, keep live progress.

Only one run at a time (protects the API credits and keeps progress simple).
Each run gets its own folder: runs/<id>/  (apps.csv, pass1.json, ..., logs/, web/).
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

from . import analyze, config, human_check, llm, progress, prompt_store, store

STAGES = ["research", "verify", "reresearch", "analysis"]
WEIGHTS = {"research": 0.55, "verify": 0.20, "reresearch": 0.20, "analysis": 0.05}

APP_COLS = ("app", "app name", "name", "company", "application", "tool")
CAT_COLS = ("category", "type", "segment")
HINT_COLS = ("hint", "website", "website / hint", "url", "domain", "docs", "site")

_lock = threading.Lock()
RUNS: dict[str, "Run"] = {}
CURRENT: str | None = None


class UploadError(ValueError):
    pass


def parse_csv(raw: bytes) -> list[dict]:
    """Accepts any CSV with an app-name column; category and website are optional."""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise UploadError("The file is empty.")
    cols = {c.strip().lower(): c for c in reader.fieldnames if c}
    pick = lambda opts: next((cols[o] for o in opts if o in cols), None)
    c_app, c_cat, c_hint = pick(APP_COLS), pick(CAT_COLS), pick(HINT_COLS)
    if not c_app:
        raise UploadError(f"No app-name column found. Use a column called 'app'. Found: {', '.join(reader.fieldnames)}")
    rows, seen = [], set()
    for r in reader:
        app = (r.get(c_app) or "").strip()
        if not app or app.lower() in seen:
            continue
        seen.add(app.lower())
        rows.append({"id": str(len(rows) + 1), "app": app[:80],
                     "category": ((r.get(c_cat) or "").strip() if c_cat else "") or "Uncategorized",
                     "hint": ((r.get(c_hint) or "").strip() if c_hint else "")[:200]})
    if not rows:
        raise UploadError("No app names found in the file.")
    if len(rows) > config.MAX_ROWS:
        raise UploadError(f"{len(rows)} apps found - the limit is {config.MAX_ROWS} per run.")
    return rows


class Run:
    def __init__(self, rows: list[dict], filename: str):
        self.id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = config.RUNS / self.id
        self.rows, self.filename = rows, filename
        self.state = "queued"          # queued | running | done | failed
        self.stage = None
        self.counts = {s: {"done": 0, "total": 0} for s in STAGES}
        self.apps = {r["app"]: {"stage": None, "status": "queued", "info": ""} for r in rows}
        self.logs: list[dict] = []
        self.error = None
        self.started = time.time()
        self.finished = None
        self.prompts: dict[str, int] = {}

    # progress events from the pipeline
    def on_event(self, e: dict):
        t = e["type"]
        if t == "stage":
            self.stage = e["stage"]
            self.counts[e["stage"]] = {"done": 0, "total": e["total"]}
            self.logs.append({"ts": _now(), "stage": e["stage"], "app": "", "msg": f"── {e['stage']}: {e['total']} apps ──"})
        elif t == "app":
            a = self.apps.setdefault(e["app"], {})
            status = e["status"]
            if status == "queued" and a.get("status") not in (None, "queued"):
                status = a["status"]  # waiting for the next stage: keep showing the last result (e.g. disputed)
            a.update(stage=e["stage"], status=status, info=e.get("info", a.get("info", "")))
            if "done" in e:
                self.counts[e["stage"]]["done"] = e["done"]
                self.logs.append({"ts": _now(), "stage": e["stage"], "app": e["app"], "msg": f"✓ {e.get('info', '')}"})
        elif t == "log":
            self.logs.append({"ts": e.get("ts", _now()), "stage": e["stage"], "app": e["app"], "msg": e["msg"]})
        if len(self.logs) > 5000:
            self.logs = self.logs[-4000:]

    def pct(self) -> float:
        p = 0.0
        for s in STAGES:
            c = self.counts[s]
            if c["total"]:
                p += WEIGHTS[s] * c["done"] / c["total"]
            elif _stage_index(self.stage) > STAGES.index(s) or self.state == "done":
                p += WEIGHTS[s]  # stage had nothing to do
        return round(100 * min(p, 1.0), 1)

    def status(self, since: int = 0) -> dict:
        return {"id": self.id, "state": self.state, "stage": self.stage, "pct": 100.0 if self.state == "done" else self.pct(),
                "counts": self.counts, "apps": self.apps, "n": len(self.rows), "filename": self.filename,
                "logs": self.logs[since:], "log_index": len(self.logs), "error": self.error,
                "elapsed": round((self.finished or time.time()) - self.started), "prompts": self.prompts}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stage_index(stage):
    return STAGES.index(stage) if stage in STAGES else -1


def start(rows: list[dict], filename: str) -> Run:
    global CURRENT
    with _lock:
        if CURRENT and RUNS[CURRENT].state in ("queued", "running"):
            raise UploadError("Another run is in progress. Please wait for it to finish.")
        run = Run(rows, filename)
        RUNS[run.id] = run
        CURRENT = run.id
    threading.Thread(target=_execute, args=(run,), daemon=True).start()
    return run


def _execute(run: Run):
    from .stages import run_reresearch, run_research, run_verify
    with _lock_pipeline:
        try:
            run.state = "running"
            llm.ABORTED = None
            config.use_workspace(run.dir / "data", run.dir / "web")
            with open(config.APPS_CSV, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["id", "app", "category", "hint"])
                w.writeheader()
                w.writerows(run.rows)
            progress.set_listener(run.on_event)
            snap = prompt_store.pin()  # this run uses these prompt versions, even if someone edits them mid-run
            store.save_json(config.DATA / "prompts_used.json", snap)
            run.prompts = {k: s["v"] for k, s in snap.items()}
            run.logs.append({"ts": _now(), "stage": "research", "app": "",
                             "msg": "prompts: " + ", ".join(f"{k} v{v}" for k, v in run.prompts.items())})
            apps = store.load_apps()
            run_research(apps)
            run_verify(apps)
            run_reresearch(apps)
            run.on_event({"type": "stage", "stage": "analysis", "total": 1})
            human_check.make_sheet(force=True)
            analyze.run()
            run.on_event({"type": "app", "stage": "analysis", "app": "summary", "status": "done", "info": "patterns + page data ready",
                          "done": 1, "total": 1})
            run.state = "done"
        except Exception as e:
            run.state, run.error = "failed", f"{e}"
            run.logs.append({"ts": _now(), "stage": run.stage, "app": "", "msg": "RUN FAILED: " + str(e)[:300]})
            traceback.print_exc()
        finally:
            run.finished = time.time()
            progress.set_listener(None)
            prompt_store.unpin()


_lock_pipeline = threading.Lock()  # config.use_workspace is global, so pipeline work is serialized


def reanalyze_with_human(run_id: str, raw: bytes) -> dict:
    """Save an uploaded, filled human-check sheet for a finished run and recompute accuracy."""
    run_dir = config.RUNS / run_id
    if not (run_dir / "data" / "pass1.json").exists():
        raise UploadError("Run not found (the server may have restarted).")
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not {"app", "field", "human_value"} <= set(rows[0].keys()):
        raise UploadError("This doesn't look like the human-check sheet (needs app, field, human_value columns).")
    if not any((r.get("human_value") or "").strip() for r in rows):
        raise UploadError("No human_value cells are filled in yet.")
    if not _lock_pipeline.acquire(blocking=False):
        raise UploadError("A run is in progress. Upload the sheet when it has finished.")
    try:
        config.use_workspace(run_dir / "data", run_dir / "web")
        (run_dir / "data" / "human_check.csv").write_text(text)
        analyze.run()
        return store.load_json(config.SUMMARY)
    finally:
        _lock_pipeline.release()


def safe_id(run_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}", run_id or ""):
        raise UploadError("Bad run id")
    return run_id

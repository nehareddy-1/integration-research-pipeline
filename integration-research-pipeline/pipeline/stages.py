"""Runs each component across all apps (a few apps in parallel), saving after every app,
so a run is safe to stop and resume. Emits progress events for the live run screen."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, llm, progress, store, tracelog
from .research import research_app, verdict_for
from .verify import fmt, verify_record

_save_lock = threading.Lock()


def _parallel(stage: str, items: list, fn, label=lambda x: x):
    """Run fn(item) for every item with PARALLEL_APPS workers; emit start/done events."""
    progress.emit({"type": "stage", "stage": stage, "total": len(items)})
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, config.PARALLEL_APPS)) as pool:
        futures = {}
        for it in items:
            progress.emit({"type": "app", "stage": stage, "app": label(it), "status": "queued"})
        for it in items:
            futures[pool.submit(_wrap, stage, it, fn, label)] = it
        for fut in as_completed(futures):
            done += 1
            try:
                app, status, info = fut.result()
            except llm.OutOfCredits:
                for f in futures:
                    f.cancel()
                raise
            print(f"   {done}/{len(items)} {app}: {info}")
            progress.emit({"type": "app", "stage": stage, "app": app, "status": status, "info": info,
                           "done": done, "total": len(items)})
    progress.emit({"type": "stage_done", "stage": stage, "total": len(items)})


def _wrap(stage, it, fn, label):
    app = label(it)
    progress.emit({"type": "app", "stage": stage, "app": app, "status": "running"})
    if llm.ABORTED:
        return app, "error", "skipped: " + llm.ABORTED
    try:
        status, info = fn(it)
        return app, status, info
    except llm.OutOfCredits:
        raise
    except Exception as e:  # one app failing never stops the run
        tracelog.log(app, stage, "error", out=str(e)[:500])
        return app, "error", f"ERROR {str(e)[:200]}"


# ------------------------------------------------------------------ [1] research
def run_research(apps: list[dict], force: bool = False):
    """[1] Research agent -> pass1.json"""
    out = store.load_json(config.PASS1)
    todo = [r for r in apps if force or r["app"] not in out]
    print(f"[1] Research: {len(todo)} apps to do ({len(apps) - len(todo)} already done)")

    def one(row):
        tracelog.clear(row["app"])  # a fresh research run restarts the app's trace
        rec, meta = research_app(row)
        with _save_lock:
            out[row["app"]] = {**rec.model_dump(), "_meta": meta}
            store.save_json(config.PASS1, out)
        return "done", f"{rec.verdict} | {rec.access} | {fmt(rec.auth_methods)}"

    _parallel("research", todo, one, label=lambda r: r["app"])


# ------------------------------------------------------------------ [2] verify
def run_verify(apps: list[dict], force: bool = False):
    """[2] Verifier agent -> flags.json"""
    pass1 = store.load_json(config.PASS1)
    out = store.load_json(config.FLAGS)
    rows = {r["app"]: r for r in apps}
    todo = [a for a in pass1 if a in rows and (force or a not in out)]
    print(f"[2] Verify: {len(todo)} apps to check")

    def one(app):
        tracelog.clear(app, "verify")
        res = verify_record(pass1[app], rows[app])
        with _save_lock:
            out[app] = res
            store.save_json(config.FLAGS, out)
        n = len(res["disputes"])
        return ("disputed" if n else "verified"), (f"{n} disputed -> {list(res['disputes'])}" if n else "all claims supported")

    _parallel("verify", todo, one)


# ------------------------------------------------------------------ [3] re-research
MERGE_EXTRA = {"access": ["access_notes", "blocker", "verdict"], "verdict": ["blocker"], "auth_methods": ["auth_notes"],
               "mcp_status": ["mcp_url"], "api_type": ["api_breadth"]}


def run_reresearch(apps: list[dict], force: bool = False):
    """[3] Re-research disputed fields, then re-verify only those fields -> pass2.json + changes.json"""
    pass1 = store.load_json(config.PASS1)
    flags = store.load_json(config.FLAGS)
    pass2 = store.load_json(config.PASS2)
    changes = store.load_json(config.CHANGES, default=[])
    rows = {r["app"]: r for r in apps}

    done = {c["app"] for c in changes} | {a for a, r in pass2.items() if r.get("_status") in ("corrected", "needs_human")}
    for app, rec in pass1.items():  # apps with nothing disputed go straight through
        if app not in rows:
            continue
        if app not in flags:  # verifier failed or was not run for this app
            if app not in pass2:
                pass2[app] = {**copy.deepcopy(rec), "_status": "unverified"}
        elif not flags[app].get("disputes") and (force or app not in pass2 or pass2[app].get("_status") == "unverified"):
            pass2[app] = {**copy.deepcopy(rec), "_status": "verified"}
    store.save_json(config.PASS2, pass2)

    todo = [a for a in pass1 if a in rows and flags.get(a, {}).get("disputes") and (force or a not in done)]
    print(f"[3] Re-research: {len(todo)} apps with disputed fields")

    def one(app):
        nonlocal changes
        disputes = flags[app]["disputes"]
        tracelog.clear(app, "reresearch")
        tracelog.clear(app, "reverify")
        try:
            new, _ = research_app(rows[app], disputes=disputes, stage="reresearch")
            new = new.model_dump()
            merged = copy.deepcopy(pass1[app])
            for f in disputes:
                merged[f] = new[f]
                for extra in MERGE_EXTRA.get(f, []):
                    merged[extra] = new[extra]
                merged["evidence"] = [e for e in merged["evidence"] if e["field"] != f] + \
                                     [e for e in new["evidence"] if e["field"] == f]
            merged["verdict"] = verdict_for(merged["access"], merged["verdict"])
            # second verification round, only on the fields that were disputed
            rv = verify_record(merged, rows[app], stage="reverify", fields=list(disputes), accept_partial=True)
            # "Do no harm": a new value is only kept if it is known AND passes its own re-check.
            # Otherwise the original pass-1 value stays and the field is marked for a human.
            kept = []
            for f in disputes:
                ok = f not in rv["disputes"] and fmt(new[f]) not in ("unknown", "")
                if not ok and fmt(merged[f]) != fmt(pass1[app][f]):
                    merged[f] = pass1[app][f]
                    for extra in MERGE_EXTRA.get(f, []):
                        merged[extra] = pass1[app][extra]
                    merged["evidence"] = [e for e in merged["evidence"] if e["field"] != f] + \
                                         [e for e in pass1[app]["evidence"] if e["field"] == f]
                    kept.append(f)
            if kept:
                tracelog.log(app, "reresearch", "kept_original", out={"fields": kept,
                             "reason": "new value was unknown or failed its re-check"})
            merged["verdict"] = verdict_for(merged["access"], merged["verdict"])
            still = rv["disputes"]
            merged["_status"] = "needs_human" if still else "corrected"
            merged["_reverify"] = rv["checks"]
            merged["_kept_original"] = kept
        except llm.OutOfCredits:
            raise
        except Exception as e:
            tracelog.log(app, "reresearch", "error", out=str(e)[:500])
            merged, rv, still, kept = {**copy.deepcopy(pass1[app]), "_status": "needs_human", "_error": str(e)[:300]}, \
                {"checks": []}, disputes, list(disputes)
        with _save_lock:
            pass2[app] = merged
            for f, d in disputes.items():
                changes = [c for c in changes if not (c["app"] == app and c["field"] == f)]
                changes.append({"app": app, "field": f, "before": d["claim"], "after": fmt(merged[f]),
                                "changed": d["claim"] != fmt(merged[f]), "why_disputed": d["why"],
                                "after_verdict": next((c["verdict"] for c in rv["checks"] if c["field"] == f), None),
                                "still_disputed": f in still, "kept_original": f in kept})
            store.save_json(config.PASS2, pass2)
            store.save_json(config.CHANGES, changes)
        return merged["_status"], f"{merged['_status']} ({len(disputes)} fields re-checked)"

    _parallel("reresearch", todo, one)
    store.save_json(config.PASS2, pass2)

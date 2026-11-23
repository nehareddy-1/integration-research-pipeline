"""Versioned system prompts.

v1 of every prompt is the default in prompts.py (read-only). Editing a prompt in the web app saves a
NEW version (v2, v3, ...) and makes it active; older versions are never overwritten, so you can switch back.

Saved in data/prompt_versions.json:
  {"research": {"active": 2, "versions": [{"v": 2, "text": "...", "note": "stricter access rule",
                                            "created": "2026-11-23T17:00:00+00:00"}]}}

Every run pins the active versions when it starts (a mid-run edit doesn't change a running job) and records
them in runs/<id>/data/prompts_used.json, so results always say which prompt versions produced them.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from . import config, prompts

PROMPTS = {
    "pick_urls": {"component": "research", "label": "Research · step 1: pick pages to read",
                  "default": prompts.PICK_URLS_PROMPT, "placeholders": ["{max_pages}"]},
    "research": {"component": "research", "label": "Research · step 2: extract the answers",
                 "default": prompts.RESEARCH_PROMPT, "placeholders": []},
    "verify": {"component": "verify", "label": "Verifier: judge each claim",
               "default": prompts.VERIFY_PROMPT, "placeholders": []},
    "reresearch_addon": {"component": "reresearch", "label": "Re-research: added to the research prompt",
                         "default": prompts.REREASEARCH_ADDON, "placeholders": ["{disputes}"]},
}

FILE = config.ROOT / "data" / "prompt_versions.json"
_lock = threading.Lock()
_pinned: dict[str, str] | None = None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    if FILE.exists():
        try:
            return json.loads(FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _save(data: dict):
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(FILE)


def versions(key: str) -> list[dict]:
    """All versions, v1 (the default) first."""
    v1 = {"v": 1, "text": PROMPTS[key]["default"], "note": "default (in code)", "created": None}
    return [v1] + _load().get(key, {}).get("versions", [])


def active_version(key: str) -> int:
    return _load().get(key, {}).get("active", 1)


def active_text(key: str) -> str:
    v = active_version(key)
    return next((x["text"] for x in versions(key) if x["v"] == v), PROMPTS[key]["default"])


def get(key: str) -> str:
    """What the pipeline calls. Returns the pinned text during a run, else the active version."""
    if _pinned is not None and key in _pinned:
        return _pinned[key]
    return active_text(key)


def pin() -> dict:
    """Freeze the active versions for a run; returns {key: {"v": n, "text": ...}} to record with the run."""
    global _pinned
    snap = {k: {"v": active_version(k), "text": active_text(k)} for k in PROMPTS}
    _pinned = {k: s["text"] for k, s in snap.items()}
    return snap


def unpin():
    global _pinned
    _pinned = None


def save_version(key: str, text: str, note: str = "") -> int:
    if key not in PROMPTS:
        raise ValueError("Unknown prompt")
    text = (text or "").strip()
    if len(text) < 40:
        raise ValueError("The prompt is too short.")
    if len(text) > 20000:
        raise ValueError("The prompt is too long (max 20,000 characters).")
    missing = [p for p in PROMPTS[key]["placeholders"] if p not in text]
    if missing:
        raise ValueError(f"Keep the placeholder {', '.join(missing)} - the code fills it in.")
    with _lock:
        data = _load()
        entry = data.setdefault(key, {"active": 1, "versions": []})
        if text == active_text(key).strip():
            raise ValueError("No changes compared with the active version.")
        v = max([1] + [x["v"] for x in entry["versions"]]) + 1
        entry["versions"].append({"v": v, "text": text, "note": (note or "").strip()[:200], "created": _now()})
        entry["active"] = v
        _save(data)
    return v


def activate(key: str, v: int):
    if key not in PROMPTS or v not in [x["v"] for x in versions(key)]:
        raise ValueError("Unknown prompt version")
    with _lock:
        data = _load()
        data.setdefault(key, {"active": 1, "versions": []})["active"] = v
        _save(data)


def catalog() -> list[dict]:
    return [{"key": k, "component": m["component"], "label": m["label"], "placeholders": m["placeholders"],
             "active": active_version(k), "versions": versions(k)} for k, m in PROMPTS.items()]

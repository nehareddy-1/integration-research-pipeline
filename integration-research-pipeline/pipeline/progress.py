"""Progress events. The CLI prints them; the web app's job runner collects them for the live screen.

Events are plain dicts:
  {"type": "stage", "stage": "research", "total": 20}
  {"type": "app",   "stage": "research", "app": "Notion", "status": "done", "info": "..."}
  {"type": "log",   "stage": "research", "app": "Notion", "msg": "search ... -> 5 results"}
"""
from __future__ import annotations

import threading
from typing import Callable

_listener: Callable[[dict], None] | None = None
_lock = threading.Lock()


def set_listener(fn: Callable[[dict], None] | None):
    global _listener
    _listener = fn


def emit(event: dict):
    fn = _listener
    if fn is not None:
        with _lock:
            fn(event)


def describe(step: str, inp, out) -> str | None:
    """Turn a trace line into one short human-readable log message (None = not worth showing)."""
    try:
        if step == "search":
            return f'search "{str(inp)[:70]}" → {len(out or [])} results'
        if step == "pick_urls":
            return f"picked {len(out.get('urls', []))} pages to read"
        if step == "scrape":
            return f"read {str(inp)[:80]} ({out.get('chars', 0)} chars{'' if out.get('ok') else ', failed'})"
        if step == "extract":
            return f"extracted → {out.get('access')} · {out.get('verdict')}"
        if step == "llm_judge":
            return f"verifier judged {inp}"
        if step == "claim_check":
            flag = " ⚑ disputed" if out.get("flagged") else ""
            return f"check {inp.get('field')} = {str(inp.get('claim'))[:40]} → {out.get('verdict')}{flag}"
        if step == "evidence_dropped":
            return f"dropped {len(out.get('urls', []))} evidence link(s) to pages not read"
        if step == "kept_original":
            return f"kept original value for {', '.join(out.get('fields', []))} (new value failed its re-check)"
        if step in ("error", "search_error"):
            return f"ERROR: {str(out)[:160]}"
    except Exception:
        return None
    return None

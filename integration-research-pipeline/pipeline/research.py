"""[1] Research agent.

For one app:
  1. search   (Composio -> Tavily)   3 queries: auth, access/pricing, MCP
  2. pick     (LLM)               choose up to 4 official pages from the results
  3. scrape   (Composio -> Firecrawl) read those pages (cached on disk)
  4. extract  (LLM)               fill AppRecord with an evidence quote + URL per field
  5. check    (code)                 drop evidence that cites a URL we never fetched

The same function runs Re-research [3] when `disputes` is passed.
"""
from __future__ import annotations

import json
import time

from . import config, llm, tools, tracelog
from . import prompt_store  # versioned prompts (v1 = defaults in prompts.py)
from .schemas import AppRecord, UrlPick

VERDICT_FROM_ACCESS = {
    "Self-serve free": "Buildable now", "Free trial": "Buildable now", "Paid plan": "Buildable with paid account",
    "Admin approval": "Needs partnership or approval", "Partner gated": "Needs partnership or approval",
    "No public API": "Not buildable",
}


def verdict_for(access: str, current: str) -> str:
    """The verdict is a rule on top of the access answer, so the two can never contradict each other."""
    return VERDICT_FROM_ACCESS.get(access, current)


def _queries(row: dict, disputes: dict | None) -> list[str]:
    app, hint = row["app"], row["hint"]
    base = [
        f"{app} API authentication developer documentation {hint}".strip(),
        f"{app} API access pricing plan developer account partner program",
        f"{app} MCP server",
    ]
    if not disputes:
        return base
    # Re-research: targeted queries for the disputed fields only
    extra = {
        "access": f"{app} API pricing which plan includes API access",
        "auth_methods": f"{app} API authentication OAuth API key",
        "api_type": f"{app} API reference REST GraphQL endpoints",
        "mcp_status": f"{app} official MCP server model context protocol",
        "verdict": f"{app} developer program app review partner approval",
    }
    return [extra[f] for f in disputes if f in extra] or base[:1]


def research_app(row: dict, disputes: dict | None = None, stage: str = "research") -> tuple[AppRecord, dict]:
    app = row["app"]
    t0 = time.time()
    tracelog.log(app, stage, "start", inp={"app": app, "category": row["category"], "hint": row["hint"],
                                          "disputes": disputes})

    # 1. search
    results: list[dict] = []
    for q in _queries(row, disputes):
        try:
            hits = tools.search(q)
        except Exception as e:
            hits = []
            tracelog.log(app, stage, "search_error", inp=q, out=str(e)[:300])
        tracelog.log(app, stage, "search", inp=q, out=[h["url"] for h in hits])
        results.extend(hits)
    seen, uniq = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            uniq.append(r)

    # 2. pick pages to read
    listing = "\n".join(f"- {r['url']} | {r['title']} | {r['content'][:200]}" for r in uniq) or "(no results)"
    pick_prompt = f"App: {app}\nWebsite hint: {row['hint']}\n\nSearch results:\n{listing}"
    if uniq:
        pick, usage = llm.generate_json(config.RESEARCH_MODEL,
                                        prompt_store.get("pick_urls").replace("{max_pages}", str(config.MAX_PAGES_PER_APP)),
                                        pick_prompt, UrlPick)
        urls = [u for u in pick.urls if u in seen][: config.MAX_PAGES_PER_APP]
        tracelog.log(app, stage, "pick_urls", inp=f"{len(uniq)} search results", out={"urls": urls, "reason": pick.reason},
                     usage=usage)
    else:
        urls = []
    hint_url = (row.get("hint") or "").split(" ")[0]
    if not urls and "." in hint_url:  # fall back to the hint domain itself
        urls = ["https://" + hint_url if "://" not in hint_url else hint_url]

    # 3. scrape
    pages = []
    for u in urls:
        p = tools.scrape(u)
        tracelog.log(app, stage, "scrape", inp=u, out={"ok": p["ok"], "via": p["via"], "chars": len(p["text"]),
                                                       "snippet": p["text"][:300]})
        if p["ok"]:
            pages.append(p)

    # 4. extract
    meta = {"app": app, "category": row["category"], "hint": row["hint"]}
    body = "\n\n".join(f"URL: {p['url']}\n{tools.page_excerpt(p)}" for p in pages) or "(no pages could be fetched)"
    snippets = "\n".join(f"- {r['url']}: {r['content'][:200]}" for r in uniq[:10])
    prompt = (f"APP_JSON: {json.dumps(meta)}\n\nSearch snippets (for orientation only, do not cite):\n{snippets}\n\n"
              f"Fetched pages:\n{body}")
    system = prompt_store.get("research")
    if disputes:
        lines = "\n".join(f"- {f} = {d['claim']!r} -> {d['why']}" for f, d in disputes.items())
        addon = prompt_store.get("reresearch_addon").replace("{disputes}", lines)
        system += "\n\n" + addon
        prompt += "\n\n" + addon
    rec, usage = llm.generate_json(config.RESEARCH_MODEL, system, prompt, AppRecord)
    rec.app, rec.category = app, row["category"]  # never let the model rename the row
    if len(rec.auth_methods) > 1:
        rec.auth_methods = [a for a in rec.auth_methods if a != "unknown"] or ["unknown"]
    if len(rec.api_type) > 1:
        rec.api_type = [t for t in rec.api_type if t != "unknown"] or ["unknown"]
    real = [t for t in rec.api_type if t in ("REST", "GraphQL", "SOAP")]
    if real:  # "X only" makes no sense next to a real API
        rec.api_type = real
    if {"OAuth2", "API key"} & set(rec.auth_methods):  # bearer header for OAuth/API-key tokens isn't a separate method
        rec.auth_methods = [a for a in rec.auth_methods if a != "Bearer token"]
    rec.verdict = verdict_for(rec.access, rec.verdict)
    for e in rec.evidence:  # strip quotation marks the model sometimes adds
        e.quote = e.quote.strip().strip('"“”\'‘’').strip()

    # 5. evidence must cite a page we actually read
    fetched = {p["url"] for p in pages}
    dropped = [e.url for e in rec.evidence if e.url not in fetched]
    rec.evidence = [e for e in rec.evidence if e.url in fetched]
    if dropped:
        tracelog.log(app, stage, "evidence_dropped", out={"reason": "cited URL was not fetched", "urls": dropped})

    meta_out = {"pages_read": [p["url"] for p in pages], "seconds": round(time.time() - t0, 1), "usage": usage}
    tracelog.log(app, stage, "extract", inp=f"{len(pages)} pages", out=rec.model_dump(), usage=usage)
    return rec, meta_out

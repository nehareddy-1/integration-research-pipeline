"""[2] Verifier agent.

It does no new research. For each checked field of a record it asks: is this claim backed by the page it cites?

Code checks first (free):  cited URL loads? official domain? quote really on the page?
Then one LLM call per app (a different model from the researcher) judges every claim:
SUPPORTED / PARTIAL / NOT_SUPPORTED.

Anything not fully supported becomes a dispute that Re-research [3] works on.
"""
from __future__ import annotations

import json
import re

from . import config, llm, tools, tracelog
from . import prompt_store
from .schemas import VerifyBatch


def fmt(v) -> str:
    return ", ".join(v) if isinstance(v, list) else str(v)


def is_unknown(v) -> bool:
    return v in ("unknown", "", None) or v == ["unknown"] or v == []


def _root(d: str) -> str:
    parts = d.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else d


def official_domain(url: str, row: dict) -> bool:
    """Same root domain as the hint, the app's name in the domain, or a GitHub/npm/PyPI path owned by the app."""
    d = tools.domain(url)
    hint_root = _root(tools.domain(row["hint"].split(" ")[0]))
    token = re.sub(r"[^a-z0-9]", "", row["app"].lower().split(" ")[0])
    if not d:
        return False
    if (hint_root and "." in hint_root and d.endswith(hint_root)) or (len(token) >= 4 and token in d):
        return True
    owner = (url.lower().split(d, 1)[-1].strip("/").split("/") + [""])[0]
    return d in ("github.com", "npmjs.com", "pypi.org") and len(token) >= 4 and token in owner


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\]\([^)]*\)", " ", s)                       # markdown links: keep the text, drop the URL
    s = s.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "\u00a0": " "}))
    s = re.sub(r"[`*_#>\[\]()|\"]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_found(quote: str, text: str) -> str:
    """'exact', 'fuzzy' (>=80% of the quote's words are on the page) or 'no'."""
    q, t = _norm(quote), _norm(text)
    if not q:
        return "no"
    if q in t:
        return "exact"
    words = [w for w in q.split() if len(w) > 2]
    if words and sum(w in t for w in words) / len(words) >= 0.8:
        return "fuzzy"
    return "no"


def _window(text: str, quote: str, size: int = 3000) -> str:
    i = _norm(text).find(_norm(quote)[:40]) if quote else -1
    if i < 0:
        return text[: size * 2]
    return text[max(0, i - size): i + size]


from .research import VERDICT_FROM_ACCESS  # noqa: E402  (same rule the researcher applies)


def verify_record(rec: dict, row: dict, stage: str = "verify", fields: list[str] | None = None,
                  accept_partial: bool = False) -> dict:
    """accept_partial=True is used on the second round: PARTIAL then counts as a warning, not a dispute."""
    app = rec["app"]
    fields = fields or config.CHECK_FIELDS
    checks, to_judge = [], []

    for f in fields:
        claim = rec.get(f)
        evs = [e for e in rec.get("evidence", []) if e["field"] == f]
        c = {"field": f, "claim": fmt(claim), "url": " | ".join(dict.fromkeys(e["url"] for e in evs)),
             "quote": " | ".join(e["quote"] for e in evs)}
        if f == "verdict":  # a conclusion, not a fact on a page: check it against the access answer by rule
            expected = VERDICT_FROM_ACCESS.get(rec.get("access"))
            ok = expected is not None and claim == expected
            c.update(verdict="SUPPORTED" if ok else "NOT_SUPPORTED", url_ok=None, official=None, quote_found=None,
                     reason=f"rule: access '{rec.get('access')}' -> '{expected}'")
            checks.append(c)
            continue
        if is_unknown(claim):
            c.update(verdict="NOT_SUPPORTED", reason="value is unknown", url_ok=None, official=None, quote_found=None)
            checks.append(c)
            continue
        if f == "mcp_status" and claim == "None found":  # absence can't be proven by quoting a page
            c.update(verdict="SUPPORTED", reason="absence of an MCP server can't be shown by a quote; accepted, worth a manual look",
                     url_ok=None, official=None, quote_found=None, warning_none_found=True)
            checks.append(c)
            continue
        if not evs:
            c.update(verdict="NOT_SUPPORTED", reason="no evidence cited", url_ok=None, official=None, quote_found=None)
            checks.append(c)
            continue
        found, oks, offs, windows = [], [], [], []
        for e in evs[:3]:
            page = tools.scrape(e["url"])  # cache hit in almost every case
            oks.append(page["ok"])
            offs.append(official_domain(e["url"], row))
            found.append(quote_found(e["quote"], page["text"]) if page["ok"] else "no")
            windows.append(f"[{e['url']}]\n" + (_window(page["text"], e["quote"], 2000) if page["ok"]
                                                 else "(page could not be loaded)"))
        c["url_ok"] = any(oks)
        c["official"] = any(offs)
        c["quote_found"] = "exact" if "exact" in found else ("fuzzy" if "fuzzy" in found else "no")
        c["_page"] = "\n\n".join(windows)
        to_judge.append(c)
        checks.append(c)

    if to_judge:
        claims_json = json.dumps([{"field": c["field"], "claim": c["claim"], "quote": c["quote"], "url": c["url"]}
                                  for c in to_judge])
        pages = "\n\n".join(f"--- page for field {c['field']} ({c['url']}) ---\n{c['_page']}" for c in to_judge)
        prompt = f"APP: {app}\nCLAIMS_JSON: {claims_json}\n\nCITED PAGE TEXT:\n{pages}"
        batch, usage = llm.generate_json(config.VERIFY_MODEL, prompt_store.get("verify"), prompt, VerifyBatch)
        by_field = {j.field: j for j in batch.checks}
        for c in to_judge:
            j = by_field.get(c["field"])
            c["verdict"] = j.verdict if j else "NOT_SUPPORTED"
            c["reason"] = j.reason if j else "verifier returned no judgement"
            c.pop("_page", None)
        tracelog.log(app, stage, "llm_judge", inp=f"{len(to_judge)} claims", out=[j.model_dump() for j in batch.checks],
                     usage=usage)

    disputes = {}
    for c in checks:
        why = None
        if c["verdict"] == "NOT_SUPPORTED" or (c["verdict"] == "PARTIAL" and not accept_partial):
            why = f"{c['verdict']}: {c['reason']}"
        elif c.get("url_ok") is False:
            why = "cited page did not load"
        elif c.get("quote_found") == "no":
            why = "quote not found on the cited page"
        # an unofficial source with an exact, supported quote is only a warning, not a dispute
        c["warning"] = ("unofficial source" if c.get("official") is False else
                        "partially supported" if c["verdict"] == "PARTIAL" else None)
        c["flagged"] = why is not None
        if why:
            disputes[c["field"]] = {"claim": c["claim"], "why": why}

    for c in checks:
        tracelog.log(app, stage, "claim_check",
                     inp={k: c.get(k) for k in ("field", "claim", "url", "quote")},
                     out={k: c.get(k) for k in ("verdict", "reason", "url_ok", "official", "quote_found", "flagged")})
    return {"app": app, "checks": checks, "disputes": disputes}

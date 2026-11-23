"""Offline fakes for PIPELINE_MOCK=1. Lets you test every stage and the web page without API keys.

A hidden 'truth' is generated per app; the fake researcher is right ~70% of the time on the
first pass and ~85% after a dispute, so the verification loop visibly improves accuracy.
None of this data is real - never publish a mock run.
"""
from __future__ import annotations

import hashlib
import json
import random
import re

from . import schemas

AUTHS = ["OAuth2", "API key", "Basic", "Bearer token"]
ACCESS = ["Self-serve free", "Free trial", "Paid plan", "Admin approval", "Partner gated"]
APIS = ["REST", "GraphQL"]
MCP = ["Official MCP", "Community MCP", "None found"]


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.md5("|".join(parts).encode()).hexdigest(), 16))


def truth(app: str) -> dict:
    r = _rng("truth", app)
    access = r.choices(ACCESS, weights=[50, 10, 15, 10, 15])[0]
    verdict = {
        "Self-serve free": "Buildable now",
        "Free trial": "Buildable now",
        "Paid plan": "Buildable with paid account",
        "Admin approval": "Needs partnership or approval",
        "Partner gated": "Needs partnership or approval",
    }[access]
    return {
        "auth_methods": sorted(set(r.choices(AUTHS, weights=[50, 35, 8, 7], k=r.choice([1, 1, 2])))),
        "access": access,
        "api_type": [r.choices(APIS, weights=[85, 15])[0]],
        "mcp_status": r.choices(MCP, weights=[35, 30, 35])[0],
        "verdict": verdict,
    }


def _maybe_wrong(app: str, field: str, value, p_right: float, salt: str):
    r = _rng(salt, app, field)
    if r.random() < p_right:
        return value
    pools = {"auth_methods": [[a] for a in AUTHS], "access": ACCESS, "api_type": [[a] for a in APIS],
             "mcp_status": MCP, "verdict": ["Buildable now", "Buildable with paid account", "Needs partnership or approval"]}
    choices = [c for c in pools[field] if c != value]
    return r.choice(choices)


def fake_search(query: str, n: int) -> list[dict]:
    app = query.split(" API")[0].split(" MCP")[0].strip()
    s = re.sub(r"[^a-z0-9]", "", app.lower()) or "app"
    urls = [f"https://developers.{s}.com/docs/auth", f"https://{s}.com/pricing",
            f"https://github.com/{s}/{s}-mcp", f"https://blog.example.com/{s}-api-review",
            f"https://docs.{s}.com/api/reference"]
    return [{"url": u, "title": f"{app} - {u.split('/')[-1]}", "content": f"About {app} API"} for u in urls[:n]]


def fake_page(url: str) -> str:
    return (f"URL {url}\nThis page describes the API. Authenticate using OAuth 2.0 or a personal API key. "
            "Developers can create a free developer account. Some features require the Enterprise plan. "
            "Our partner program is required for marketplace listing. We publish an MCP server. REST endpoints for all objects.")


def fake_llm(schema, prompt: str):
    if schema is schemas.UrlPick:
        urls = re.findall(r"https?://\S+", prompt)
        urls = [u.rstrip('",') for u in urls if "blog.example" not in u]
        return schemas.UrlPick(urls=list(dict.fromkeys(urls))[:4], reason="official docs and pricing (mock)")

    if schema is schemas.AppRecord:
        meta = json.loads(re.search(r"APP_JSON: (\{.*?\})\n", prompt).group(1))
        app, cat = meta["app"], meta["category"]
        disputed = re.findall(r"^- (\w+) =", prompt, flags=re.M) if "A reviewer disputed" in prompt else []
        t = truth(app)
        vals = {}
        for f in t:
            if disputed and f in disputed:
                vals[f] = _maybe_wrong(app, f, t[f], 0.85, "pass2")
            else:
                vals[f] = _maybe_wrong(app, f, t[f], 0.70, "pass1")
        urls = re.findall(r"^URL: (\S+)", prompt, flags=re.M) or ["https://example.com"]
        ev = [schemas.Evidence(field=f, url=urls[i % len(urls)], quote="Authenticate using OAuth 2.0 or a personal API key")
              for i, f in enumerate(t)]
        blocker = "none" if vals["verdict"] == "Buildable now" else (
            "API only on Enterprise plan" if vals["access"] == "Paid plan" else "Partner program required")
        return schemas.AppRecord(
            app=app, category=cat, one_liner=f"{app} (mock one-liner)",
            auth_methods=vals["auth_methods"], auth_notes="mock", access=vals["access"], access_notes="mock",
            api_type=vals["api_type"], api_breadth=_rng("b", app).choice(["Narrow", "Medium", "Broad"]),
            docs_url=urls[0], mcp_status=vals["mcp_status"],
            mcp_url=urls[0] if vals["mcp_status"] != "None found" else "",
            verdict=vals["verdict"], blocker=blocker, evidence=ev,
            confidence=_rng("c", app).choice(["med", "high", "high"]),
        )

    if schema is schemas.VerifyBatch:
        claims = json.loads(re.search(r"CLAIMS_JSON: (\[.*?\])\n", prompt).group(1))
        app = re.search(r"APP: (.+)", prompt).group(1).strip()
        t = truth(app)
        out = []
        for c in claims:
            f, claimed = c["field"], c["claim"]
            right = (claimed == t.get(f)) or (isinstance(t.get(f), list) and claimed == ", ".join(t[f]))
            r = _rng("verify", app, f, str(claimed)).random()
            if right:
                v = "SUPPORTED" if r < 0.92 else "PARTIAL"
            else:
                v = "NOT_SUPPORTED" if r < 0.8 else "SUPPORTED"
            out.append(schemas.ClaimCheck(field=f, claim=str(claimed), verdict=v, reason="mock judgement"))
        return schemas.VerifyBatch(checks=out)

    raise ValueError(f"no mock for {schema}")

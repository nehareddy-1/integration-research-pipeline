"""[5] Analysis: accuracy (pass 1 vs pass 2 vs human) + patterns across all 100 apps.

Plain Python, no LLM. Writes data/summary.json and exports everything the web page needs to web/data/.
"""
from __future__ import annotations

import csv
import json
import shutil
from collections import Counter, defaultdict

from . import config, store, tracelog
from .human_check import read_sheet
from .verify import fmt

SELF_SERVE = {"Self-serve free", "Free trial"}
GATED = {"Admin approval", "Partner gated"}

BLOCKER_BUCKETS = [  # (bucket, keywords) - checked in order
    ("Partner program / contact sales", ["partner", "sales", "contact", "business verification", "verified business"]),
    ("App review / admin approval", ["review", "approval", "approve", "admin", "marketplace", "allowlist", "whitelist"]),
    ("Paid or enterprise plan", ["paid", "enterprise", "plan", "subscription", "pricing", "premium", "tier", "trial"]),
    ("No public API", ["no public api", "no api", "not documented", "undocumented", "cli", "library", "open-source tool"]),
]


def _set(v) -> frozenset:
    if isinstance(v, list):
        items = v
    else:
        items = str(v).replace(";", ",").split(",")
    return frozenset(i.strip().lower() for i in items if i and i.strip())


def same(a, b) -> bool:
    return _set(a) == _set(b)


def bucket(blocker: str, verdict: str) -> str | None:
    if verdict == "Buildable now":
        return None
    b = (blocker or "").lower()
    for name, keys in BLOCKER_BUCKETS:
        if any(k in b for k in keys):
            return name
    return "Other / unclear"


# ------------------------------------------------------------------ accuracy
def accuracy(pass1: dict, pass2: dict) -> dict:
    rows = [r for r in read_sheet() if (r.get("human_value") or "").strip()]
    if not rows:
        return {"available": False, "note": "Human check sheet not filled yet."}
    per_field = defaultdict(lambda: {"n": 0, "p1": 0, "p2": 0})
    results, n, p1_ok, p2_ok = [], 0, 0, 0
    for r in rows:
        app, f, truth = r["app"], r["field"], r["human_value"]
        if app not in pass1:
            continue
        v1, v2 = pass1[app].get(f), pass2.get(app, pass1[app]).get(f)
        ok1, ok2 = same(v1, truth), same(v2, truth)
        n += 1
        p1_ok += ok1
        p2_ok += ok2
        pf = per_field[f]
        pf["n"] += 1
        pf["p1"] += ok1
        pf["p2"] += ok2
        results.append({"app": app, "field": f, "human": truth, "pass1": fmt(v1), "pass2": fmt(v2),
                        "pass1_ok": ok1, "pass2_ok": ok2, "source_url": r.get("source_url", ""),
                        "notes": r.get("notes", "")})
    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0  # "unknown" or blank minutes are simply not counted
    minutes = sum(_num(r.get("minutes_spent")) for r in read_sheet())
    return {
        "available": True,
        "apps_checked": len({r["app"] for r in results}),
        "fields_checked": n,
        "pass1": round(p1_ok / n, 3) if n else None,
        "pass2": round(p2_ok / n, 3) if n else None,
        "by_field": {f: {"n": v["n"], "pass1": round(v["p1"] / v["n"], 3), "pass2": round(v["p2"] / v["n"], 3)}
                     for f, v in per_field.items()},
        "fixed_by_loop": [x for x in results if not x["pass1_ok"] and x["pass2_ok"]],
        "broken_by_loop": [x for x in results if x["pass1_ok"] and not x["pass2_ok"]],
        "still_wrong": [x for x in results if not x["pass2_ok"]],
        "human_minutes": minutes,
        "rows": results,
    }


def verifier_stats(flags: dict, pass2: dict) -> dict:
    """Automatic accuracy proxy: share of claims the verifier could confirm, before and after the loop."""
    total = sum(len(v["checks"]) for v in flags.values())
    flagged1 = sum(len(v["disputes"]) for v in flags.values())
    still = sum(1 for r in pass2.values() for c in r.get("_reverify", []) if c.get("flagged"))
    reasons = Counter()
    for v in flags.values():
        for c in v["checks"]:
            if c.get("flagged"):
                if c.get("verdict") != "SUPPORTED":
                    reasons[c["verdict"]] += 1
                elif c.get("url_ok") is False:
                    reasons["page did not load"] += 1
                elif c.get("quote_found") == "no":
                    reasons["quote not on page"] += 1
                else:
                    reasons["other"] += 1
    return {
        "claims_checked": total,
        "flagged_pass1": flagged1,
        "supported_rate_pass1": round(1 - flagged1 / total, 3) if total else None,
        "still_flagged_pass2": still,
        "supported_rate_pass2": round(1 - still / total, 3) if total else None,
        "flag_reasons": dict(reasons.most_common()),
        "apps_flagged": sum(1 for v in flags.values() if v["disputes"]),
        "unofficial_source_warnings": sum(1 for v in flags.values() for c in v["checks"] if c.get("warning")),
        "status_counts": dict(Counter(r.get("_status", "unverified") for r in pass2.values())),
    }


# ------------------------------------------------------------------ patterns
def patterns(recs: dict, apps: list[dict]) -> dict:
    cats = [c for c in dict.fromkeys(a["category"] for a in apps)]
    rs = [recs[a["app"]] for a in apps if a["app"] in recs]
    auth = Counter(m for r in rs for m in set(r["auth_methods"]))
    access_by_cat = {c: Counter() for c in cats}
    verdict_by_cat = {c: Counter() for c in cats}
    for r in rs:
        access_by_cat[r["category"]][r["access"]] += 1
        verdict_by_cat[r["category"]][r["verdict"]] += 1
    cat_size = dict(Counter(r["category"] for r in rs))
    self_serve_by_cat = {c: sum(v for k, v in access_by_cat[c].items() if k in SELF_SERVE) for c in cats}
    gated_by_cat = {c: sum(v for k, v in access_by_cat[c].items() if k in GATED) for c in cats}
    blockers = Counter(b for r in rs if (b := bucket(r.get("blocker", ""), r["verdict"])))
    easy = [r["app"] for r in rs if r["verdict"] == "Buildable now" and r["access"] in SELF_SERVE
            and r.get("api_breadth") in ("Medium", "Broad")]
    outreach = [r["app"] for r in rs if r["verdict"] == "Needs partnership or approval" or r["access"] in GATED]
    paid = [r["app"] for r in rs if r["verdict"] == "Buildable with paid account"]
    return {
        "n": len(rs),
        "categories": cats,
        "category_size": cat_size,
        "auth_counts": dict(auth.most_common()),
        "oauth_and_key": sum(1 for r in rs if {"OAuth2", "API key"} <= set(r["auth_methods"])),
        "access_counts": dict(Counter(r["access"] for r in rs).most_common()),
        "access_by_category": {c: dict(v) for c, v in access_by_cat.items()},
        "self_serve_by_category": self_serve_by_cat,
        "gated_by_category": gated_by_cat,
        "verdict_counts": dict(Counter(r["verdict"] for r in rs).most_common()),
        "verdict_by_category": {c: dict(v) for c, v in verdict_by_cat.items()},
        "api_type_counts": dict(Counter(t for r in rs for t in set(r["api_type"])).most_common()),
        "breadth_counts": dict(Counter(r.get("api_breadth", "unknown") for r in rs).most_common()),
        "mcp_counts": dict(Counter(r["mcp_status"] for r in rs).most_common()),
        "blocker_buckets": dict(blockers.most_common()),
        "easy_wins": easy,
        "needs_outreach": outreach,
        "paid_only": paid,
        "unknown_fields": sum(1 for r in rs for f in config.CHECK_FIELDS if fmt(r.get(f)) in ("unknown", "")),
    }


def headlines(p: dict, acc: dict, vs: dict) -> list[str]:
    """Plain sentences built from the numbers (no LLM, so nothing is invented). Edit before publishing."""
    n = p["n"] or 1
    h = []
    if p["auth_counts"]:
        top, cnt = next(iter(p["auth_counts"].items()))
        second = list(p["auth_counts"].items())[1] if len(p["auth_counts"]) > 1 else None
        s = f"{top} dominates: {cnt} of {n} apps support it"
        if second:
            s += f", then {second[0]} ({second[1]})"
        h.append(s + f"; {p['oauth_and_key']} offer both OAuth2 and API keys.")
    ss = sum(v for k, v in p["access_counts"].items() if k in SELF_SERVE)
    ranked = sorted(p["categories"], key=lambda c: p["self_serve_by_category"][c], reverse=True)
    if ranked:
        h.append(f"{ss} of {n} apps are self-serve. Most open: {ranked[0]} ({p['self_serve_by_category'][ranked[0]]}/{p['category_size'].get(ranked[0], 0)}). "
                 f"Most gated: {max(p['categories'], key=lambda c: p['gated_by_category'][c])}.")
    if p["blocker_buckets"]:
        b, c = next(iter(p["blocker_buckets"].items()))
        h.append(f"The most common blocker is '{b}' ({c} apps).")
    h.append(f"{len(p['easy_wins'])} easy wins can be built today; {len(p['needs_outreach'])} need partner or approval outreach; "
             f"{len(p['paid_only'])} need a paid account.")
    om = p["mcp_counts"].get("Official MCP", 0)
    h.append(f"{om} apps already ship an official MCP server.")
    if acc.get("available"):
        h.append(f"Accuracy on a {acc['apps_checked']}-app checked sample went from "
                 f"{acc['pass1']:.0%} (first pass) to {acc['pass2']:.0%} after the verification loop.")
    elif vs.get("supported_rate_pass1") is not None:
        h.append(f"The verifier confirmed {vs['supported_rate_pass1']:.0%} of claims on the first pass and "
                 f"{vs['supported_rate_pass2']:.0%} after re-research.")
    return h


def run_stats() -> dict:
    steps, usage_in, usage_out = Counter(), 0, 0
    first, last = None, None
    for p in config.LOGS.glob("*.jsonl"):
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            l = json.loads(line)
            steps[f"{l['stage']}:{l['step']}"] += 1
            u = l.get("usage") or {}
            usage_in += u.get("input_tokens", 0)
            usage_out += u.get("output_tokens", 0)
            first = min(first or l["ts"], l["ts"])
            last = max(last or l["ts"], l["ts"])
    return {"steps": dict(steps), "searches": sum(v for k, v in steps.items() if k.endswith(":search")),
            "pages_scraped": sum(v for k, v in steps.items() if k.endswith(":scrape")),
            "llm_calls": sum(v for k, v in steps.items() if k.endswith((":pick_urls", ":extract", ":llm_judge"))),
            "input_tokens": usage_in, "output_tokens": usage_out, "started": first, "finished": last,
            "mock": config.MOCK}


# ------------------------------------------------------------------ export
def export_web(apps, pass1, pass2, flags, changes, summary):
    out_rows = []
    for a in apps:
        r = pass2.get(a["app"]) or pass1.get(a["app"])
        if not r:
            continue
        p1 = pass1.get(a["app"], {})
        out_rows.append({
            "id": int(a["id"]), "app": a["app"], "category": a["category"], "hint": a["hint"],
            **{k: r.get(k) for k in ("one_liner", "auth_methods", "auth_notes", "access", "access_notes", "api_type",
                                     "api_breadth", "docs_url", "mcp_status", "mcp_url", "verdict", "blocker",
                                     "confidence", "evidence")},
            "status": r.get("_status", "unverified"),
            "pass1": {f: fmt(p1.get(f)) for f in config.CHECK_FIELDS},
            "disputes": flags.get(a["app"], {}).get("disputes", {}),
        })
    store.save_json(config.WEB_DATA / "results.json", out_rows)
    store.save_json(config.WEB_DATA / "summary.json", summary)
    store.save_json(config.WEB_DATA / "changes.json", changes)

    traces = config.WEB_DATA / "traces"
    traces.mkdir(exist_ok=True)
    for a in apps:
        lines = tracelog.read(a["app"])
        for l in lines:  # keep trace files small
            if l["step"] == "extract" and isinstance(l.get("output"), dict):
                l["output"] = {k: l["output"].get(k) for k in config.CHECK_FIELDS + ["confidence", "blocker"]}
        if lines:
            store.save_json(traces / f"{tracelog.slug(a['app'])}.json", lines)

    # flat CSV for spreadsheets / other agents
    with open(config.WEB_DATA / "results.csv", "w", newline="") as f:
        cols = ["id", "app", "category", "one_liner", "auth_methods", "access", "api_type", "api_breadth",
                "mcp_status", "verdict", "blocker", "confidence", "status", "docs_url", "evidence_urls"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in out_rows:
            w.writerow({**{c: fmt(r.get(c)) for c in cols if c != "evidence_urls"},
                        "evidence_urls": " ".join(dict.fromkeys(e["url"] for e in r["evidence"] or []))})
    if config.HUMAN_CHECK.exists():
        shutil.copy(config.HUMAN_CHECK, config.WEB_DATA / "human_check.csv")


def run():
    apps = store.load_apps()
    pass1 = store.load_json(config.PASS1)
    pass2 = store.load_json(config.PASS2) or pass1
    flags = store.load_json(config.FLAGS)
    changes = store.load_json(config.CHANGES, default=[])
    if not pass1:
        print("Nothing to analyze yet - run the research stage first.")
        return
    acc = accuracy(pass1, pass2)
    vs = verifier_stats(flags, pass2)
    pat = patterns(pass2, apps)
    summary = {
        "headlines": headlines(pat, acc, vs),
        "patterns": pat,
        "accuracy": acc,
        "verifier": vs,
        "run": run_stats(),
        "changes_count": len(changes),
        "changed_values": sum(1 for c in changes if c["changed"]),
        "prompts_used": {k: v["v"] for k, v in store.load_json(config.DATA / "prompts_used.json").items()},
        "models": {"provider": config.LLM_PROVIDER, "research": config.RESEARCH_MODEL, "verify": config.VERIFY_MODEL},
    }
    store.save_json(config.SUMMARY, summary)
    export_web(apps, pass1, pass2, flags, changes, summary)
    print("[5] Analysis written to data/summary.json and web/data/")
    for line in summary["headlines"]:
        print("   -", line)

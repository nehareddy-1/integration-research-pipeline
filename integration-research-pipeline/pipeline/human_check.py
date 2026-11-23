"""[4] Human check: builds a BLIND checking sheet for a person to fill in.

The sheet does not show the agent's answers, so the checker can't be anchored by them.
After it is filled, analyze.py compares it against pass 1 and pass 2.
"""
from __future__ import annotations

import csv
import random
from collections import defaultdict

from . import config, store

ALLOWED = {
    "auth_methods": "one or more of: OAuth2; API key; Basic; Bearer token; JWT; Other  (separate with ;)",
    "access": "Self-serve free | Free trial | Paid plan | Admin approval | Partner gated | No public API",
    "api_type": "one or more of: REST; GraphQL; SOAP; SDK only; CLI only; Webhooks only; None",
    "mcp_status": "Official MCP | Community MCP | None found",
    "verdict": "Buildable now | Buildable with paid account | Needs partnership or approval | Not buildable",
}

COLUMNS = ["app", "category", "field", "allowed_values", "docs_hint", "human_value", "source_url", "notes", "minutes_spent"]


def make_sheet(n: int = 20, seed: int = 7, force: bool = False):
    if config.HUMAN_CHECK.exists() and not force:
        filled = [r for r in read_sheet() if r.get("human_value")]
        if filled:
            print(f"[4] {config.HUMAN_CHECK.name} already has {len(filled)} filled rows - not overwriting (use --force).")
            return
    done = store.load_json(config.PASS2) or store.load_json(config.PASS1)
    apps = [a for a in store.load_apps() if a["app"] in done]
    flags = store.load_json(config.FLAGS)
    rng = random.Random(seed)

    # 2 apps per category (stratified), preferring one that the verifier disputed and one it didn't,
    # so the sample covers both easy and tricky cases.
    by_cat = defaultdict(list)
    for r in apps:
        by_cat[r["category"]].append(r)
    picked = []
    per_cat = max(1, n // max(len(by_cat), 1))
    for cat, rows in by_cat.items():
        rng.shuffle(rows)
        disputed = [r for r in rows if flags.get(r["app"], {}).get("disputes")]
        clean = [r for r in rows if r not in disputed]
        pool = (disputed[:1] + clean[:1] + rows)
        seen = []
        for r in pool:
            if r not in seen:
                seen.append(r)
        picked += seen[:per_cat]

    with open(config.HUMAN_CHECK, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in picked:
            for field in config.CHECK_FIELDS:
                w.writerow({"app": r["app"], "category": r["category"], "field": field,
                            "allowed_values": ALLOWED[field], "docs_hint": r["hint"],
                            "human_value": "", "source_url": "", "notes": "", "minutes_spent": ""})
    print(f"[4] Wrote {config.HUMAN_CHECK} with {len(picked)} apps x {len(config.CHECK_FIELDS)} fields.")
    print("    Open it in Google Sheets/Excel, fill human_value + source_url from the real docs, save as CSV,")
    print("    then run:  python -m pipeline.run_all --stage analyze")


def read_sheet() -> list[dict]:
    if not config.HUMAN_CHECK.exists():
        return []
    with open(config.HUMAN_CHECK, newline="") as f:
        return list(csv.DictReader(f))

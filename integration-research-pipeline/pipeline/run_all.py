"""Command line entry point.

  python -m pipeline.run_all --stage agents --limit 5      # research + verify + re-research on 5 apps (test)
  python -m pipeline.run_all --stage agents                # all 100 (safe to stop and re-run; it resumes)
  python -m pipeline.run_all --stage sample                # write the blind human-check sheet
  ... fill data/human_check.csv by hand ...
  python -m pipeline.run_all --stage analyze               # accuracy + patterns -> web/data/

Single stages: research | verify | reresearch | sample | analyze.   --apps "Notion,Slack" to pick apps.
"""
from __future__ import annotations

import argparse
import time

from . import analyze, config, human_check, llm, prompt_store, store, tools
from .stages import run_reresearch, run_research, run_verify


def main():
    ap = argparse.ArgumentParser(description="Integration research pipeline")
    ap.add_argument("--stage", default="agents",
                    choices=["research", "verify", "reresearch", "agents", "sample", "analyze"])
    ap.add_argument("--limit", type=int, help="only the first N apps (for testing)")
    ap.add_argument("--apps", help="comma-separated app names")
    ap.add_argument("--force", action="store_true", help="redo apps that are already done")
    args = ap.parse_args()

    apps = store.load_apps(only=args.apps.split(",") if args.apps else None, limit=args.limit)
    if config.MOCK:
        print("*** MOCK MODE: fake tools + fake LLM, results are NOT real ***")
    t0 = time.time()

    if args.stage in ("research", "verify", "reresearch", "agents"):
        snap = prompt_store.pin()
        store.save_json(config.DATA / "prompts_used.json", snap)
        print("Prompts: " + ", ".join(f"{k} v{s['v']}" for k, s in snap.items()))
    if args.stage in ("research", "agents"):
        run_research(apps, args.force)
    if args.stage in ("verify", "agents"):
        run_verify(apps, args.force)
    if args.stage in ("reresearch", "agents"):
        run_reresearch(apps, args.force)
    if args.stage == "agents":
        human_check.make_sheet()
        print("\n⏸  Agents done. Next: fill data/human_check.csv by hand, then run --stage analyze")
    if args.stage == "sample":
        human_check.make_sheet(force=args.force)
    if args.stage == "analyze":
        analyze.run()

    print(f"\nDone in {time.time() - t0:.0f}s | LLM calls {llm.STATS['llm_calls']} | "
          f"searches {tools.STATS['searches']} | scrapes {tools.STATS['scrapes']} "
          f"(cache hits {tools.STATS['scrape_cache_hits']}, http fallbacks {tools.STATS['scrape_fallbacks']})")


if __name__ == "__main__":
    main()

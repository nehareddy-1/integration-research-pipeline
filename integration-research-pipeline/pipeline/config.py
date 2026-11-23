"""Central settings. Every value can be overridden in the .env file."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional at import time
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(ROOT / ".env")

# ---------- paths ----------
DATA = ROOT / "data"
CACHE = DATA / "cache"          # scraped pages, reused by the verifier (saves Firecrawl credits)
LOGS = DATA / "logs"            # one JSONL trace file per app
WEB_DATA = ROOT / "web" / "data"

RUNS = ROOT / "runs"            # one folder per web-app run (each is its own workspace)

APPS_CSV = DATA / "apps.csv"
PASS1 = DATA / "pass1.json"
FLAGS = DATA / "flags.json"
PASS2 = DATA / "pass2.json"
CHANGES = DATA / "changes.json"
HUMAN_CHECK = DATA / "human_check.csv"
SUMMARY = DATA / "summary.json"

for p in (DATA, CACHE, LOGS, WEB_DATA, RUNS):
    p.mkdir(parents=True, exist_ok=True)


def use_workspace(data_dir: Path, web_dir: Path | None = None):
    """Point every stage at another data folder (the web app gives each run its own).
    The page cache stays shared so a page is never downloaded twice."""
    global DATA, LOGS, WEB_DATA, APPS_CSV, PASS1, FLAGS, PASS2, CHANGES, HUMAN_CHECK, SUMMARY
    DATA = Path(data_dir)
    LOGS = DATA / "logs"
    WEB_DATA = Path(web_dir) if web_dir else DATA / "web"
    APPS_CSV, PASS1, FLAGS = DATA / "apps.csv", DATA / "pass1.json", DATA / "flags.json"
    PASS2, CHANGES = DATA / "pass2.json", DATA / "changes.json"
    HUMAN_CHECK, SUMMARY = DATA / "human_check.csv", DATA / "summary.json"
    for p in (DATA, LOGS, WEB_DATA):
        p.mkdir(parents=True, exist_ok=True)

# ---------- keys ----------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()   # "openai" or "gemini"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID", "default")

# ---------- models ----------
# The verifier uses a different (stronger) model so it doesn't repeat the researcher's mistakes.
_DEFAULTS = {
    "openai": ("gpt-5.4-mini", "gpt-4.1-mini", "200"),   # verifier: a different, cheaper model family
    "gemini": ("gemini-3.1-flash-lite", "gemini-3.6-flash", "8"),   # free tier: see aistudio.google.com/rate-limit
}
_r, _v, _rpm = _DEFAULTS.get(LLM_PROVIDER, _DEFAULTS["openai"])
RESEARCH_MODEL = os.getenv("RESEARCH_MODEL", _r)
VERIFY_MODEL = os.getenv("VERIFY_MODEL", _v)
LLM_RPM = int(os.getenv("LLM_RPM", _rpm))                      # requests per minute cap
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low")   # optional: low | medium | high

# ---------- Composio tool slugs (swap here if your project uses different ones) ----------
SEARCH_TOOL = os.getenv("SEARCH_TOOL", "TAVILY_SEARCH")
SCRAPE_TOOL = os.getenv("SCRAPE_TOOL", "FIRECRAWL_SCRAPE")

# ---------- budgets ----------
SEARCH_RESULTS_PER_QUERY = int(os.getenv("SEARCH_RESULTS_PER_QUERY", "5"))
MAX_PAGES_PER_APP = int(os.getenv("MAX_PAGES_PER_APP", "4"))
MAX_CHARS_PER_PAGE = int(os.getenv("MAX_CHARS_PER_PAGE", "7000"))
PARALLEL_APPS = int(os.getenv("PARALLEL_APPS", "10"))           # apps processed at the same time
MAX_ROWS = int(os.getenv("MAX_ROWS", "100"))                   # web upload limit
MOCK_DELAY = float(os.getenv("MOCK_DELAY", "0"))               # seconds per fake LLM call (to preview animations)

# Offline test mode: no network, fake tools + fake LLM. PIPELINE_MOCK=1
MOCK = os.getenv("PIPELINE_MOCK", "0") == "1"

# Fields that the verifier and the human check compare
CHECK_FIELDS = ["auth_methods", "access", "api_type", "mcp_status", "verdict"]

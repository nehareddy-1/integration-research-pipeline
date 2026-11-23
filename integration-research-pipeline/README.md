# Integration research pipeline

An agent that researches whether apps can become AI-agent toolkits: auth method, self-serve vs gated access,
API surface, existing MCP, a buildability verdict, and an evidence URL + quote for every answer.
It then checks itself, re-researches what failed, and is checked by a human on a blind sample.

Built for the Composio AI Product Ops take-home (100 apps in `data/apps.csv`).

## How it works

```
data/apps.csv (100 apps)
  [1] Research agent   Composio→Tavily search, LLM picks official pages,
                       Composio→Firecrawl reads them, LLM fills the schema      → data/pass1.json
  [2] Verifier agent   code checks (page loads, official domain, quote on page)
                       + a different, stronger model judges each claim          → data/flags.json
  [3] Re-research      only disputed fields, targeted searches, then re-verify  → data/pass2.json, data/changes.json
  [4] Human check      blind sheet, 2 apps per category                         → data/human_check.csv
  [5] Analysis         accuracy pass 1 vs pass 2, patterns (plain Python)       → data/summary.json, web/data/
  [6] Web page         web/index.html reads web/data/*.json
```

| Component | Calls an LLM | Composio tools | System prompt | Output schema |
|---|---|---|---|---|
| 1 Research | OpenAI `RESEARCH_MODEL` (gpt-5.4-mini) | search + scrape | `PICK_URLS_PROMPT`, `RESEARCH_PROMPT` | `UrlPick`, `AppRecord` |
| 2 Verifier | OpenAI `VERIFY_MODEL` (gpt-5.4) | scrape (cached) | `VERIFY_PROMPT` | `VerifyBatch` |
| 3 Re-research | OpenAI `RESEARCH_MODEL` | search + scrape | research prompt + `REREASEARCH_ADDON` | `AppRecord` |
| 4 Human check | no | no | – | CSV columns |
| 5 Analysis | no | no | – | `summary.json` |

Every step writes a line to `data/logs/<app>.jsonl` (input, output, tokens). The page shows these as a per-app trace.

```
pipeline/   config.py  schemas.py  prompts.py  llm.py  tools.py  tracelog.py  store.py
            research.py  verify.py  stages.py  human_check.py  analyze.py  run_all.py  mock.py
            progress.py  jobs.py  connect_tools.py
api/        main.py        FastAPI: serves the page, upload, live progress, results, human-check upload
web/        index.html  app.js  style.css   (upload → run → results screens)
data/       apps.csv  pass1.json  flags.json  pass2.json  changes.json  human_check.csv  summary.json  logs/  cache/
```

## Setup (about 15 minutes)

**1. Python 3.11 or newer** (the Composio SDK needs it; macOS ships 3.9).
```bash
brew install python@3.12            # or download from python.org
cd ~/Desktop/integration-research-pipeline
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**2. Keys.** `cp .env.example .env`, then fill in:
- `OPENAI_API_KEY` from https://platform.openai.com/api-keys (the account needs API credits).
  Defaults: `gpt-5.4-mini` for research, `gpt-5.4` for verification. Change them with `RESEARCH_MODEL` / `VERIFY_MODEL`.
  (To use Gemini's free tier instead: `LLM_PROVIDER=gemini` and `GEMINI_API_KEY`.)
- `COMPOSIO_API_KEY` from the Composio dashboard: your project → Settings → API Keys.

**3. Connect the tools in Composio.** Sign up free at tavily.com and firecrawl.dev and copy their API keys.
In the Composio dashboard: Auth Configs → Create Auth Config → **Tavily** → API Key, and the same for **Firecrawl**.
Then create a connected account for each with user id `default` (or whatever you put in `COMPOSIO_USER_ID`).

**4. Test without any keys** (fake data, proves the wiring):
```bash
PIPELINE_MOCK=1 python -m pipeline.run_all --stage agents --limit 5
```
Delete `data/pass1.json data/flags.json data/pass2.json data/changes.json data/human_check.csv data/logs data/cache` afterwards.

## Run it in the browser (the web app)

```bash
uvicorn api.main:app --reload        # then open http://localhost:8000
```

1. **Upload screen:** click **Run sample** (15 apps from `web/sample_apps.csv`) or drop a CSV with an `app` column (`category` and `website` optional, max 100 rows).
   `GET /api/template.csv` gives an example.
2. **Run screen:** the flow diagram lights up stage by stage (Research → Verify → Re-research → Analysis),
   with a % progress bar, per-app status chips and a live log. 10 apps are processed in parallel (`PARALLEL_APPS`).
3. **Results screen:** patterns, the full table (click a row for evidence + trace), how it works, verification.
   In Verification: **Download human-check sheet → fill it → Upload filled sheet** to get pass 1 vs pass 2 accuracy.

Each run is saved under `runs/<run-id>/`. Only one run at a time.
Preview the screens without keys: `PIPELINE_MOCK=1 MOCK_DELAY=0.5 uvicorn api.main:app`.

### Editing the system prompts (versioned)

On the upload screen, click a pipeline step (Research, Verify, Re-research) to see its system prompt.
The default text from `pipeline/prompts.py` is **v1** and is never changed. Edit the text, add a note
("what changed?") and click **Save as v2**. The new version becomes active, and every earlier version stays in
the dropdown so you can switch back with **Use vN**.

- Versions are stored in `data/prompt_versions.json` (commit it to keep your versions; on Render, download it with
  the "download versions" link, because the free server's disk is wiped on restart).
- Each run pins the active versions when it starts (an edit mid-run doesn't affect it) and records them in
  `runs/<id>/data/prompts_used.json`; the results page shows them under "Run stats".
- That makes A/B testing simple: run the sample with v1, run it again with v2, upload the same human-check answers
  to both, and compare pass 1 / pass 2 accuracy.

## Run from the command line (same pipeline)

```bash
python -m pipeline.run_all --stage agents --limit 5      # research + verify + re-research on 5 apps
python -m pipeline.run_all --stage agents                # all apps in data/apps.csv; resumes if stopped
```
Then fill `data/human_check.csv` (blind: don't look at the agent's answers; use the values in `allowed_values`,
separate several with `;`) and run:
```bash
python -m pipeline.run_all --stage analyze               # accuracy + patterns -> web/data/
```

## Deploy (Render, free)

1. Push the repo to GitHub (`.env`, `runs/` and `data/cache/` are git-ignored).
2. Render → New → Blueprint → choose the repo. `render.yaml` creates one web service that serves the page and runs the agent.
3. In the service's Environment tab add `OPENAI_API_KEY` and `COMPOSIO_API_KEY`. Tavily and Firecrawl are already
   connected inside Composio, so they need nothing here.

Notes on the free plan: the service sleeps when idle (first visit takes about a minute), and files written by runs
are lost when it restarts or redeploys, so download results from the Data section. A run keeps going if the
browser tab is closed; reopen the page to watch it. Anyone with the link can start a run (max `MAX_ROWS` apps,
one at a time), which spends your OpenAI credits.

## Budget (100 apps)

| Service | Used | Cost |
|---|---|---|
| Tavily searches | ~300 + re-research | free (1,000 credits / month) |
| Firecrawl pages | ~400 + re-research (verifier reuses the cache) | free (1,000 credits / month) |
| OpenAI | ~3 calls per app + re-research, ~2-3M tokens | a few dollars; check the 5-app run's token count first |
| Composio tool calls | ~800 | free plan |

If Firecrawl fails or runs out, `tools.scrape` falls back to a plain HTTP fetch (works for server-rendered docs).
`LLM_RPM` spaces out LLM calls; lower it if you see 429 errors. `OPENAI_REASONING_EFFORT=low` makes runs cheaper and faster.

## Where a human is needed

- Creating accounts and keys, connecting Tavily + Firecrawl in Composio.
- Apps marked `needs_human` (still disputed after re-research).
- The blind accuracy check on 20 apps.
- Reading the generated headlines against the numbers before publishing.

## Troubleshooting

- **`TAVILY_SEARCH failed` / tool not found:** your Composio project may use a different slug. Check the toolkit page in the
  dashboard and set `SEARCH_TOOL` / `SCRAPE_TOOL` in `.env`.
- **No connected account:** the tools run as `COMPOSIO_USER_ID`; it must match the user id of your Tavily/Firecrawl connections.
- **OpenAI `model_not_found`:** your key can't use that model; set `RESEARCH_MODEL` / `VERIFY_MODEL` (e.g. `gpt-4.1-mini`).
- **OpenAI `insufficient_quota`:** add credits at platform.openai.com/settings/billing.

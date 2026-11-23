"""Web app: serves the page and runs the pipeline on an uploaded CSV.

  GET  /                                  the app (web/index.html)
  GET  /api/template.csv                  example upload file
  POST /api/runs            (file)        start a run (max MAX_ROWS apps, one run at a time)
  GET  /api/current                       the run in progress / last run, if any
  GET  /api/runs/{id}/status?since=N      progress, per-app status, new log lines
  GET  /api/runs/{id}/data/{path}         results files (summary.json, results.json, traces/<app>.json, ...)
  POST /api/runs/{id}/human-check (file)  upload the filled human-check sheet -> accuracy recomputed
  GET  /api/prompts                       every system prompt with all versions (v1 = default)
  POST /api/prompts/{key}   {text, note}  save an edited prompt as a new version (becomes active)
  POST /api/prompts/{key}/activate {version}   switch the active version
  GET  /api/prompts/export                download data/prompt_versions.json

Run locally:  uvicorn api.main:app --reload   then open http://localhost:8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from pipeline import config, jobs, prompt_store

app = FastAPI(title="Integration research agent")
WEB = config.ROOT / "web"


@app.get("/api/health")
def health():
    return {"ok": True, "mock": config.MOCK, "max_rows": config.MAX_ROWS, "current": jobs.CURRENT}


@app.get("/api/template.csv", response_class=PlainTextResponse)
def template():
    rows = ["app,category,website", "Notion,Productivity,developers.notion.com", "Stripe,Finance,stripe.com/docs/api",
            "Zendesk,Support,zendesk.com"]
    return PlainTextResponse("\n".join(rows) + "\n", media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=apps_template.csv"})


@app.post("/api/runs")
async def create_run(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(400, "File too large.")
    try:
        rows = jobs.parse_csv(raw)
        run = jobs.start(rows, file.filename or "upload.csv")
    except jobs.UploadError as e:
        raise HTTPException(409 if "in progress" in str(e) else 400, str(e))
    return {"id": run.id, "n": len(rows)}


@app.get("/api/current")
def current():
    if not jobs.CURRENT:
        return {"id": None}
    r = jobs.RUNS[jobs.CURRENT]
    return {"id": r.id, "state": r.state}


@app.get("/api/runs/{run_id}/status")
def status(run_id: str, since: int = 0):
    run = jobs.RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found (the server may have restarted).")
    return run.status(since)


@app.get("/api/runs/{run_id}/data/{path:path}")
def run_file(run_id: str, path: str):
    try:
        jobs.safe_id(run_id)
    except jobs.UploadError:
        raise HTTPException(400, "Bad run id")
    base = (config.RUNS / run_id).resolve()
    target = (base / ("data/human_check.csv" if path == "human_check.csv" else f"web/{path}")).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target, headers={"Cache-Control": "no-store"})


@app.post("/api/runs/{run_id}/human-check")
async def human_check(run_id: str, file: UploadFile = File(...)):
    try:
        summary = jobs.reanalyze_with_human(jobs.safe_id(run_id), await file.read())
    except jobs.UploadError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "accuracy": summary.get("accuracy")}


class PromptIn(BaseModel):
    text: str
    note: str = ""


class ActivateIn(BaseModel):
    version: int


@app.get("/api/prompts")
def list_prompts():
    return prompt_store.catalog()


@app.post("/api/prompts/{key}")
def save_prompt(key: str, body: PromptIn):
    try:
        v = prompt_store.save_version(key, body.text, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "version": v, "catalog": prompt_store.catalog()}


@app.post("/api/prompts/{key}/activate")
def activate_prompt(key: str, body: ActivateIn):
    try:
        prompt_store.activate(key, body.version)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "catalog": prompt_store.catalog()}


@app.get("/api/prompts/export")
def export_prompts():
    data = prompt_store.FILE.read_text() if prompt_store.FILE.exists() else "{}"
    return PlainTextResponse(data, media_type="application/json",
                             headers={"Content-Disposition": "attachment; filename=prompt_versions.json"})


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=WEB), name="web")

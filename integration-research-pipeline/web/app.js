// Three screens: upload -> live run -> results.  Talks to api/main.py.
// If no API is reachable (static hosting) and data/summary.json exists, it opens straight on the results.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v) => (Array.isArray(v) ? v.join(", ") : v ?? "");
const pct = (x) => (x == null ? "–" : Math.round(x * 100) + "%");
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const ACCESS_ORDER = ["Self-serve free", "Free trial", "Paid plan", "Admin approval", "Partner gated", "No public API", "unknown"];
const ACCESS_COLOR = { "Self-serve free": "#2f9e44", "Free trial": "#8ce99a", "Paid plan": "#f08c00", "Admin approval": "#fd7e14", "Partner gated": "#e03131", "No public API": "#868e96", unknown: "#ced4da" };
const VERDICT_ORDER = ["Buildable now", "Buildable with paid account", "Needs partnership or approval", "Not buildable", "unknown"];
const VERDICT_COLOR = { "Buildable now": "#2f9e44", "Buildable with paid account": "#f08c00", "Needs partnership or approval": "#e03131", "Not buildable": "#495057", unknown: "#ced4da" };
const STATUS = { verified: ["Verified", "#2f9e44"], corrected: ["Corrected", "#3b5bdb"], needs_human: ["Needs human", "#f08c00"], unverified: ["Unverified", "#868e96"] };

const FLOW = [
  { key: "research", title: "Research", desc: "Search, read official docs, fill the schema with evidence", prompts: ["pick_urls", "research"] },
  { key: "verify", title: "Verify", desc: "Check every claim against the cited page", prompts: ["verify"] },
  { key: "reresearch", title: "Re-research", desc: "Re-check disputed fields, verify again", prompts: ["reresearch_addon"] },
  { key: "analysis", title: "Analysis", desc: "Patterns, stats and this report", prompts: [] },
];
let PROMPTS = [];  // catalog from /api/prompts

const pill = (text, color) => `<span class="pill" style="background:${color}22;color:${color};border-color:${color}55">${esc(text)}</span>`;
const bars = (obj, color = "var(--accent)") => {
  const entries = Object.entries(obj || {});
  const max = Math.max(1, ...entries.map((e) => e[1]));
  return entries.map(([k, v]) => `<div class="bar"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${(v / max) * 100}%;background:${color}"></div></div><span class="v">${v}</span></div>`).join("") || '<p class="muted">No data</p>';
};
const table = (cols, rows) => rows.length
  ? `<table><thead><tr>${cols.map((c) => `<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`
  : '<p class="muted">None.</p>';

async function getJSON(path, opts) {
  const r = await fetch(path, { cache: "no-store", ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText || path);
  return body;
}

function show(screen) {
  ["upload", "run", "results"].forEach((s) => ($(`#screen-${s}`).hidden = s !== screen));
  window.scrollTo(0, 0);
}

// ---------------------------------------------------------------- flow diagram
function renderFlow(el, counts = {}, stage = null, state = "idle") {
  const idx = FLOW.findIndex((f) => f.key === stage);
  el.innerHTML = FLOW.map((f, i) => {
    const c = counts[f.key] || { done: 0, total: 0 };
    let cls = state === "idle" ? "idle" : "pending";
    if (state === "done" || idx > i) cls = "done";
    else if (idx === i && state === "running") cls = "active";
    const frac = cls === "done" ? 1 : c.total ? c.done / c.total : 0;
    const versions = f.prompts.map((k) => PROMPTS.find((p) => p.key === k)).filter(Boolean);
    const editLine = !f.prompts.length ? `<div class="edit off">No LLM · plain code</div>`
      : `<div class="edit">✎ Edit prompt · ${[...new Set(versions.map((p) => "v" + p.active))].join(" / ") || "v1"}${versions.some((p) => p.active > 1) ? "" : " (default)"}</div>`;
    const counter = state === "idle" ? "" : cls === "done" ? (c.total ? `✓ ${c.done}/${c.total}` : "✓ nothing to do")
      : cls === "active" ? `${c.done}/${c.total} apps` : "waiting";
    const node = `<div class="fnode ${cls}"><div class="ft"><span class="badge">${cls === "done" ? "✓" : i + 1}</span>${f.title}</div>
      <div class="fd">${f.desc}</div>${state === "idle" ? editLine : `<div class="fc">${counter}</div><div class="mini"><i style="width:${frac * 100}%"></i></div>`}</div>`;
    const conn = i < FLOW.length - 1 ? `<div class="fconn ${cls === "done" ? (idx === i + 1 && state === "running" ? "active" : "done") : ""}"></div>` : "";
    return node + conn;
  }).join("");
  if (state === "idle") el.querySelectorAll(".fnode").forEach((n, i) => n.addEventListener("click", () => openPrompts(FLOW[i])));
}

// ---------------------------------------------------------------- prompt editor (versioned)
async function loadPrompts() {
  try { PROMPTS = await getJSON("api/prompts"); } catch { PROMPTS = []; }
  renderFlow($("#flow-upload"));
}

async function openPrompts(step) {
  const body = $("#prompt-body");
  if (step.prompts.length && !PROMPTS.length) {
    try { PROMPTS = await getJSON("api/prompts"); } catch (e) {
      body.innerHTML = `<p class="eyebrow">Pipeline step</p><h2>${step.title}</h2>
        <p class="error">Couldn't load the prompts from the server (${esc(e.message)}).</p>
        <p class="muted">The server is probably still running the older code. Stop it with Ctrl+C and start it again:
        <code>uvicorn api.main:app --reload</code>, then refresh this page.</p>`;
      $("#prompt-modal").hidden = false;
      return;
    }
  }
  if (!step.prompts.length) {
    body.innerHTML = `<p class="eyebrow">Pipeline step</p><h2>${step.title}</h2><p>${step.desc}.</p>
      <p class="muted">This step doesn't call an LLM, so it has no system prompt. It's plain Python (<code>pipeline/analyze.py</code>):
      it counts auth methods, groups access by category, buckets blockers by keywords and builds the headlines from the numbers,
      so nothing in the report is invented.</p>`;
    $("#prompt-modal").hidden = false;
    return;
  }
  body.innerHTML = `<p class="eyebrow">Pipeline step · system prompt${step.prompts.length > 1 ? "s" : ""}</p><h2>${step.title}</h2><p class="muted">${step.desc}.</p>` +
    step.prompts.map((k) => `<div class="pblock" data-key="${k}"></div>`).join("");
  step.prompts.forEach((k) => renderPromptBlock(k));
  $("#prompt-modal").hidden = false;
}

function renderPromptBlock(key, showV) {
  const p = PROMPTS.find((x) => x.key === key);
  const el = document.querySelector(`.pblock[data-key="${key}"]`);
  if (!p || !el) return;
  const shown = showV || p.active;
  const ver = p.versions.find((v) => v.v === shown) || p.versions[0];
  const next = Math.max(...p.versions.map((v) => v.v)) + 1;
  el.innerHTML = `
    <div class="pblock-head"><b>${esc(p.label)}</b>
      <select class="pv">${p.versions.map((v) => `<option value="${v.v}" ${v.v === shown ? "selected" : ""}>v${v.v}${v.v === 1 ? " · default" : ""}${v.note && v.v !== 1 ? " · " + esc(v.note) : ""}${v.v === p.active ? " (active)" : ""}</option>`).join("")}</select></div>
    ${p.placeholders.length ? `<p class="muted small" style="margin:6px 0 0">Keep ${p.placeholders.map((x) => `<code>${esc(x)}</code>`).join(", ")}: the code fills it in.</p>` : ""}
    <textarea class="pt" spellcheck="false">${esc(ver.text)}</textarea>
    <div class="row">
      <input class="pnote" placeholder="What changed? (optional, e.g. stricter access rule)">
      <button class="primary psave" disabled>Save as v${next}</button>
      ${shown !== p.active ? `<button class="ghost pact">Use v${shown}</button>` : `<span class="muted small">v${shown} is active</span>`}
    </div>
    <div class="pmsg"></div>`;
  const ta = el.querySelector(".pt"), save = el.querySelector(".psave"), msg = el.querySelector(".pmsg");
  ta.addEventListener("input", () => (save.disabled = ta.value.trim() === ver.text.trim()));
  el.querySelector(".pv").addEventListener("change", (e) => renderPromptBlock(key, +e.target.value));
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const r = await getJSON(`api/prompts/${key}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: ta.value, note: el.querySelector(".pnote").value }) });
      PROMPTS = r.catalog;
      renderPromptBlock(key);
      renderFlow($("#flow-upload"));
      document.querySelector(`.pblock[data-key="${key}"] .pmsg`).innerHTML = `<span style="color:var(--ok)">Saved as v${r.version}. The next run uses it.</span>`;
    } catch (e) {
      msg.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`;
      save.disabled = false;
    }
  });
  const act = el.querySelector(".pact");
  if (act) act.addEventListener("click", async () => {
    try {
      const r = await getJSON(`api/prompts/${key}/activate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: shown }) });
      PROMPTS = r.catalog;
      renderPromptBlock(key);
      renderFlow($("#flow-upload"));
      document.querySelector(`.pblock[data-key="${key}"] .pmsg`).innerHTML = `<span style="color:var(--ok)">v${shown} is now active.</span>`;
    } catch (e) { msg.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  });
}

// ---------------------------------------------------------------- screen 1: upload
let FILE = null;

function parseCsvPreview(text) {
  const rows = [];
  let row = [], cur = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"' && text[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') q = false; else cur += ch; }
    else if (ch === '"') q = true;
    else if (ch === ",") { row.push(cur); cur = ""; }
    else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cur); rows.push(row); row = []; cur = ""; }
    else cur += ch;
  }
  if (cur || row.length) { row.push(cur); rows.push(row); }
  return rows.filter((r) => r.some((c) => c.trim()));
}

async function onFile(file) {
  const err = $("#upload-error");
  err.hidden = true;
  FILE = null;
  $("#run-btn").disabled = true;
  $("#run-btn").textContent = "Run agent ▶";
  if (!file) return;
  const text = (await file.text()).replace(/^﻿/, "");
  const rows = parseCsvPreview(text);
  const head = (rows[0] || []).map((h) => h.trim().toLowerCase());
  const appCol = head.findIndex((h) => ["app", "app name", "name", "company", "application", "tool"].includes(h));
  $("#file-info").hidden = false;
  $("#file-name").textContent = file.name;
  if (appCol < 0) {
    $("#file-count").textContent = "no app column";
    $("#file-preview").innerHTML = "";
    err.textContent = "Couldn't find an app-name column. Add a header called 'app' (see the template).";
    err.hidden = false;
    return;
  }
  const names = [...new Set(rows.slice(1).map((r) => (r[appCol] || "").trim()).filter(Boolean).map((n) => n.toLowerCase()))];
  const max = +$("#max-rows").textContent || 100;
  $("#file-count").textContent = `${names.length} apps found`;
  $("#file-preview").innerHTML = `<table><thead><tr>${rows[0].map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${
    rows.slice(1, 6).map((r) => `<tr>${rows[0].map((_, i) => `<td>${esc(r[i] || "")}</td>`).join("")}</tr>`).join("")}${
    rows.length > 6 ? `<tr><td colspan="${rows[0].length}" class="muted">… and ${rows.length - 6} more</td></tr>` : ""}</tbody></table>`;
  if (!names.length) { err.textContent = "No app names found in the file."; err.hidden = false; return; }
  if (names.length > max) { err.textContent = `${names.length} apps found - the limit is ${max} per run.`; err.hidden = false; return; }
  FILE = file;
  $("#run-btn").disabled = false;
  $("#run-btn").textContent = `Run agent on ${names.length} apps ▶`;
}

async function runSample() {
  const btn = $("#sample-btn");
  btn.disabled = true;
  try {
    const r = await fetch("sample_apps.csv", { cache: "no-store" });
    if (!r.ok) throw new Error("sample file not found");
    await onFile(new File([await r.blob()], "sample_apps.csv", { type: "text/csv" }));
    if (FILE) $("#run-btn").click();
  } catch (e) {
    $("#upload-error").textContent = "Could not load the sample: " + e.message;
    $("#upload-error").hidden = false;
  } finally {
    btn.disabled = false;
  }
}

function initUpload() {
  renderFlow($("#flow-upload"));
  $("#prompt-close").addEventListener("click", () => ($("#prompt-modal").hidden = true));
  $("#prompt-modal").addEventListener("click", (e) => e.target.id === "prompt-modal" && ($("#prompt-modal").hidden = true));
  const dz = $("#dropzone");
  $("#file-input").addEventListener("change", (e) => onFile(e.target.files[0]));
  $("#sample-btn").addEventListener("click", runSample);
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => onFile(e.dataTransfer.files[0]));
  $("#file-clear").addEventListener("click", () => { $("#file-input").value = ""; $("#file-info").hidden = true; onFile(null); $("#file-input").click(); });
  $("#run-btn").addEventListener("click", async () => {
    if (!FILE) return;
    $("#run-btn").disabled = true;
    $("#run-btn").textContent = "Starting…";
    const fd = new FormData();
    fd.append("file", FILE);
    try {
      const r = await getJSON("api/runs", { method: "POST", body: fd });
      location.hash = `run=${r.id}`;
      watchRun(r.id);
    } catch (e) {
      $("#upload-error").textContent = e.message;
      $("#upload-error").hidden = false;
      $("#run-btn").disabled = false;
      $("#run-btn").textContent = "Run agent ▶";
    }
  });
}

// ---------------------------------------------------------------- screen 2: live run
const STAGE_LABEL = { research: "Researching", verify: "Verifying", reresearch: "Re-researching disputed fields", analysis: "Analysing" };
const CHIP_LEGEND = [["#3b5bdb", "working"], ["#2b8a3e", "done / verified"], ["#d9480f", "disputed"],
  ["#1971c2", "corrected"], ["#e67700", "needs human"], ["#e03131", "error"]];

async function watchRun(id) {
  show("run");
  $("#app-legend").innerHTML = CHIP_LEGEND.map(([c, l]) => `<span><i class="dot" style="background:${c}"></i>${l}</span>`).join(" &nbsp; ");
  $("#log").innerHTML = "";
  $("#run-error").hidden = true;
  let since = 0, shownPct = 0;
  for (;;) {
    let s;
    try {
      s = await getJSON(`api/runs/${id}/status?since=${since}`);
    } catch (e) {
      $("#run-title").textContent = "Lost the run";
      $("#run-error").hidden = false;
      $("#run-error").innerHTML = `<p>${esc(e.message)}</p><button class="ghost" onclick="location.hash='';location.reload()">Back to upload</button>`;
      return;
    }
    since = s.log_index;
    $("#run-file").textContent = s.filename;
    $("#run-title").textContent = s.state === "done" ? `Done: ${s.n} apps researched` : s.state === "failed" ? "Run failed" : `Researching ${s.n} apps`;
    $("#run-elapsed").textContent = mmss(s.elapsed);
    renderFlow($("#flow-run"), s.counts, s.stage, s.state === "queued" ? "running" : s.state);
    shownPct = Math.max(shownPct, s.pct);
    $("#run-bar").style.width = shownPct + "%";
    $("#run-bar").classList.toggle("running", s.state === "running");
    $("#run-pct").textContent = Math.floor(shownPct) + "%";
    const c = s.counts[s.stage] || { done: 0, total: 0 };
    $("#run-stage-label").textContent = s.state === "running" && s.stage ? `${STAGE_LABEL[s.stage]} · ${c.done} of ${c.total}` : "";
    $("#app-chips").innerHTML = Object.entries(s.apps).map(([name, a]) => {
      const cls = a.status;
      return `<span class="achip ${esc(cls)}" title="${esc(a.info || "")}">${esc(name)}</span>`;
    }).join("");
    const log = $("#log");
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.insertAdjacentHTML("beforeend", s.logs.map((l) => {
      const t = (l.ts || "").slice(11, 19);
      const cls = l.msg.startsWith("──") ? "sep" : /ERROR|FAILED/.test(l.msg) ? "err" : l.msg.startsWith("✓") ? "ok" : "";
      return `<div class="l ${cls}"><span class="t">${t}</span> ${l.app ? `<span class="a">${esc(l.app)}</span> ` : ""}${esc(l.msg)}</div>`;
    }).join(""));
    if (log.children.length > 1500) [...log.children].slice(0, log.children.length - 1200).forEach((n) => n.remove());
    if (atBottom) log.scrollTop = log.scrollHeight;

    if (s.state === "done") { await sleep(1500); return openResults(`api/runs/${id}/data/`, id); }
    if (s.state === "failed") {
      $("#run-error").hidden = false;
      $("#run-error").innerHTML = `<h3>Run failed</h3><p>${esc(s.error)}</p><button class="ghost" onclick="location.hash='';location.reload()">Back to upload</button>`;
      return;
    }
    await sleep(1200);
  }
}

// ---------------------------------------------------------------- screen 3: results
let RESULTS = [], BASE = "", RUN_ID = null;

async function openResults(base, runId) {
  BASE = base;
  RUN_ID = runId;
  let summary, changes;
  try {
    [summary, RESULTS, changes] = await Promise.all([getJSON(base + "summary.json"), getJSON(base + "results.json"), getJSON(base + "changes.json").catch(() => [])]);
  } catch (e) {
    show("upload");
    $("#upload-error").textContent = "Could not load results: " + e.message;
    $("#upload-error").hidden = false;
    return;
  }
  show("results");
  if (summary.run?.mock) $("#mock-banner").hidden = false;
  $("#results-meta").textContent = `${RESULTS.length} apps${runId ? ` · run ${runId}` : ""}`;
  renderPatterns(summary);
  renderTable();
  renderAgent(summary);
  renderVerification(summary, changes);
  renderDownloads();
}

function renderPatterns(s) {
  const p = s.patterns, a = s.accuracy, v = s.verifier;
  $("#headlines").innerHTML = s.headlines.map((h) => `<li>${esc(h)}</li>`).join("");
  const selfServe = (p.access_counts["Self-serve free"] || 0) + (p.access_counts["Free trial"] || 0);
  const kpis = [
    [p.n, "apps researched"], [selfServe, "self-serve credentials"], [p.verdict_counts["Buildable now"] || 0, "buildable today"],
    [p.mcp_counts["Official MCP"] || 0, "ship an official MCP"],
    a.available ? [`${pct(a.pass1)} → ${pct(a.pass2)}`, "accuracy, pass 1 → pass 2 (checked sample)"]
      : [`${pct(v.supported_rate_pass1)} → ${pct(v.supported_rate_pass2)}`, "claims verified, pass 1 → pass 2"],
  ];
  $("#kpis").innerHTML = kpis.map(([b, t]) => `<div class="kpi"><b>${esc(b)}</b><span>${esc(t)}</span></div>`).join("");
  $("#chart-auth").innerHTML = bars(p.auth_counts);
  $("#chart-blockers").innerHTML = bars(p.blocker_buckets, "var(--bad)");
  const cols = ACCESS_ORDER.filter((k) => p.access_counts[k]);
  const max = Math.max(1, ...p.categories.flatMap((c) => cols.map((k) => p.access_by_category[c][k] || 0)));
  $("#matrix").innerHTML = `<table class="matrix"><thead><tr><th>Category</th>${cols.map((k) => `<th>${esc(k)}</th>`).join("")}<th>Self-serve</th></tr></thead><tbody>${
    p.categories.map((c) => `<tr><td>${esc(c)}</td>${cols.map((k) => {
      const n = p.access_by_category[c][k] || 0;
      return `<td style="background:${ACCESS_COLOR[k]}${Math.round((n / max) * 200).toString(16).padStart(2, "0")}">${n || ""}</td>`;
    }).join("")}<td><b>${p.self_serve_by_category[c]}/${p.category_size[c]}</b></td></tr>`).join("")}</tbody></table>`;
  const vcols = VERDICT_ORDER.filter((k) => p.verdict_counts[k]);
  $("#verdict-legend").innerHTML = vcols.map((k) => `<span><i style="background:${VERDICT_COLOR[k]}"></i>${esc(k)} (${p.verdict_counts[k]})</span>`).join("");
  $("#chart-verdict").innerHTML = p.categories.map((c) => {
    const total = p.category_size[c] || 1;
    return `<div class="stack"><span>${esc(c)}</span><div class="seg-row">${vcols.map((k) => {
      const n = p.verdict_by_category[c][k] || 0;
      return n ? `<div class="seg" title="${esc(k)}: ${n}" style="width:${(n / total) * 100}%;background:${VERDICT_COLOR[k]}"></div>` : "";
    }).join("")}</div></div>`;
  }).join("");
  const chips = (list) => list.map((n) => `<span class="chip" data-app="${esc(n)}">${esc(n)}</span>`).join("") || '<span class="muted">None</span>';
  $("#list-easy").innerHTML = chips(p.easy_wins);
  $("#list-outreach").innerHTML = chips(p.needs_outreach);
  $("#list-paid").innerHTML = chips(p.paid_only);
  document.querySelectorAll(".chip[data-app]").forEach((el) => el.addEventListener("click", () => openApp(el.dataset.app)));
}

function renderTable() {
  const reset = (sel, label) => (sel.innerHTML = `<option value="">${label}</option>`);
  const fill = (sel, values) => values.forEach((v) => sel.insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`));
  reset($("#f-cat"), "All categories"); reset($("#f-access"), "All access types"); reset($("#f-verdict"), "All verdicts"); reset($("#f-status"), "All check statuses");
  fill($("#f-cat"), [...new Set(RESULTS.map((r) => r.category))]);
  fill($("#f-access"), ACCESS_ORDER.filter((a) => RESULTS.some((r) => r.access === a)));
  fill($("#f-verdict"), VERDICT_ORDER.filter((a) => RESULTS.some((r) => r.verdict === a)));
  Object.entries(STATUS).forEach(([k, [label]]) => RESULTS.some((r) => r.status === k) && $("#f-status").insertAdjacentHTML("beforeend", `<option value="${k}">${label}</option>`));
  ["#f-q", "#f-cat", "#f-access", "#f-verdict", "#f-status"].forEach((id) => ($(id).oninput = drawRows));
  drawRows();
}

function drawRows() {
  const q = $("#f-q").value.toLowerCase(), c = $("#f-cat").value, a = $("#f-access").value, v = $("#f-verdict").value, st = $("#f-status").value;
  const rows = RESULTS.filter((r) => (!q || r.app.toLowerCase().includes(q)) && (!c || r.category === c) && (!a || r.access === a) && (!v || r.verdict === v) && (!st || r.status === st));
  $("#f-count").textContent = `${rows.length} of ${RESULTS.length}`;
  $("#results tbody").innerHTML = rows.map((r) => {
    const [sl, sc] = STATUS[r.status] || STATUS.unverified;
    const urls = [...new Set((r.evidence || []).map((e) => e.url))];
    return `<tr data-app="${esc(r.app)}"><td>${r.id}</td><td><b>${esc(r.app)}</b><div class="muted small">${esc(r.one_liner)}</div></td><td>${esc(r.category)}</td>
      <td>${esc(fmt(r.auth_methods))}</td><td>${pill(r.access, ACCESS_COLOR[r.access] || "#868e96")}</td>
      <td>${esc(fmt(r.api_type))}<div class="muted small">${esc(r.api_breadth)}</div></td><td>${esc(r.mcp_status)}</td>
      <td>${pill(r.verdict, VERDICT_COLOR[r.verdict] || "#868e96")}</td><td class="small">${esc(r.blocker === "none" ? "" : r.blocker)}</td>
      <td>${pill(sl, sc)}</td><td class="small">${urls.slice(0, 2).map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">src${i + 1}</a>`).join(" ")}</td></tr>`;
  }).join("");
  document.querySelectorAll("#results tbody tr").forEach((tr) => tr.addEventListener("click", () => openApp(tr.dataset.app)));
}

async function openApp(name) {
  const r = RESULTS.find((x) => x.app === name);
  if (!r) return;
  const [sl, sc] = STATUS[r.status] || STATUS.unverified;
  const diffs = Object.entries(r.pass1 || {}).filter(([f, v]) => v !== fmt(r[f]));
  $("#drawer-body").innerHTML = `
    <p class="eyebrow">${esc(r.category)} · #${r.id}</p><h2>${esc(r.app)} ${pill(sl, sc)}</h2><p>${esc(r.one_liner)}</p>
    <dl class="stats"><dt>Auth</dt><dd>${esc(fmt(r.auth_methods))} <span class="muted">${esc(r.auth_notes)}</span></dd>
    <dt>Access</dt><dd>${esc(r.access)} <span class="muted">${esc(r.access_notes)}</span></dd>
    <dt>API</dt><dd>${esc(fmt(r.api_type))} · ${esc(r.api_breadth)}</dd>
    <dt>MCP</dt><dd>${esc(r.mcp_status)} ${r.mcp_url ? `<a href="${esc(r.mcp_url)}" target="_blank" rel="noopener">link</a>` : ""}</dd>
    <dt>Verdict</dt><dd>${esc(r.verdict)}</dd><dt>Blocker</dt><dd>${esc(r.blocker)}</dd>
    <dt>Docs</dt><dd><a href="${esc(r.docs_url)}" target="_blank" rel="noopener">${esc(r.docs_url)}</a></dd><dt>Confidence</dt><dd>${esc(r.confidence)}</dd></dl>
    ${diffs.length ? `<h3 style="margin-top:16px">Changed by the verification loop</h3>${table([["Field", (d) => esc(d[0])], ["Pass 1", (d) => esc(d[1])], ["Final", (d) => esc(fmt(r[d[0]]))], ["Why disputed", (d) => esc(r.disputes?.[d[0]]?.why || "")]], diffs)}` : ""}
    <h3 style="margin-top:16px">Evidence</h3>${(r.evidence || []).map((e) => `<div class="ev"><b>${esc(e.field)}</b>: “${esc(e.quote)}”<br><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.url)}</a></div>`).join("") || '<p class="muted">No evidence kept.</p>'}
    <h3 style="margin-top:16px">Agent trace</h3><div id="trace" class="muted">Loading…</div>`;
  $("#drawer").hidden = false;
  try {
    const t = await getJSON(`${BASE}traces/${slug(r.app)}.json`);
    $("#trace").outerHTML = `<div class="trace">${t.map((l) => `<div class="t"><b>${esc(l.stage)} · ${esc(l.step)}</b> <span class="muted small">${esc(l.ts)}</span>
      ${l.input != null ? `<pre>in: ${esc(typeof l.input === "string" ? l.input : JSON.stringify(l.input, null, 1))}</pre>` : ""}
      ${l.output != null ? `<pre>out: ${esc(typeof l.output === "string" ? l.output : JSON.stringify(l.output, null, 1))}</pre>` : ""}</div>`).join("")}</div>`;
  } catch {
    $("#trace").textContent = "No trace file for this app.";
  }
}

function renderAgent(s) {
  const a = s.accuracy, v = s.verifier, run = s.run;
  const items = [
    "Creating the Composio, OpenAI, Tavily and Firecrawl accounts and keys, and connecting Tavily + Firecrawl in Composio.",
    `Reviewing apps the loop could not settle: ${v.status_counts?.needs_human || 0} app(s) still had a disputed field after re-research.`,
    a.available ? `Accuracy check of ${a.apps_checked} apps (${a.fields_checked} fields) against the real docs${a.human_minutes ? `, about ${Math.round(a.human_minutes)} minutes` : ""}.`
      : "Accuracy check of a sample against the real docs (download the sheet in the Verification section).",
    "Reading the generated headlines against the numbers before publishing.",
  ];
  $("#human-needed").innerHTML = items.map((i) => `<li>${esc(i)}</li>`).join("");
  const dl = [
    ["Models", `${s.models.research} (research) · ${s.models.verify} (verify)`],
    ["Prompt versions", Object.entries(s.prompts_used || {}).map(([k, v]) => `${k} v${v}`).join(" · ") || "defaults (v1)"],
    ["Web searches", run.searches], ["Pages read", run.pages_scraped], ["LLM calls", run.llm_calls],
    ["Tokens", `${(run.input_tokens / 1000).toFixed(0)}k in · ${(run.output_tokens / 1000).toFixed(0)}k out`],
    ["Run window", run.started ? `${run.started} → ${run.finished}` : "–"],
  ];
  $("#run-stats").innerHTML = dl.map(([k, val]) => `<dt>${esc(k)}</dt><dd>${esc(val)}</dd>`).join("");
}

function renderVerification(s, changes) {
  const a = s.accuracy, v = s.verifier;
  $("#hc-card").hidden = !RUN_ID;
  if (RUN_ID) $("#hc-download").href = `${BASE}human_check.csv`;
  if (a.available) {
    $("#acc-main").innerHTML = `<div class="acc-bars">${bars({ "Pass 1 (agent only)": Math.round(a.pass1 * 100), "Pass 2 (after loop)": Math.round(a.pass2 * 100) }, "var(--ok)")}</div>
      <p class="sub">${a.fields_checked} fields on ${a.apps_checked} apps, checked against the docs. Values in %.</p>`;
    $("#acc-fields").innerHTML = table([["Field", (r) => esc(r[0])], ["n", (r) => r[1].n], ["Pass 1", (r) => pct(r[1].pass1)], ["Pass 2", (r) => `<b>${pct(r[1].pass2)}</b>`]], Object.entries(a.by_field));
    const cols = [["App", (r) => esc(r.app)], ["Field", (r) => esc(r.field)], ["Pass 1", (r) => esc(r.pass1)], ["Final", (r) => esc(r.pass2)], ["Human", (r) => `<b>${esc(r.human)}</b>`]];
    $("#fixed").innerHTML = table(cols, a.fixed_by_loop);
    $("#still-wrong").innerHTML = table([...cols, ["Notes", (r) => esc(r.notes)]], a.still_wrong);
  } else {
    $("#acc-main").innerHTML = '<p class="muted">Not measured yet: upload the filled check sheet above. Until then, the verifier stats below are the automatic check.</p>';
    $("#acc-fields").innerHTML = '<p class="muted">–</p>';
    $("#fixed").innerHTML = $("#still-wrong").innerHTML = '<p class="muted">Waiting for the accuracy check.</p>';
  }
  const dl = [
    ["Claims checked", v.claims_checked], ["Disputed on pass 1", `${v.flagged_pass1} (${v.apps_flagged} apps)`],
    ["Still disputed after loop", v.still_flagged_pass2], ["Verified rate", `${pct(v.supported_rate_pass1)} → ${pct(v.supported_rate_pass2)}`],
    ...Object.entries(v.flag_reasons || {}).map(([k, n]) => [`  reason: ${k}`, n]),
    ["Warnings (partial support / unofficial source)", v.unofficial_source_warnings ?? 0],
  ];
  $("#verifier-stats").innerHTML = dl.map(([k, val]) => `<dt>${esc(k)}</dt><dd>${esc(val)}</dd>`).join("");
  $("#changes-count").textContent = `(${changes.length} fields re-checked, ${changes.filter((c) => c.changed).length} changed)`;
  $("#changes").innerHTML = table([["App", (c) => esc(c.app)], ["Field", (c) => esc(c.field)], ["Before", (c) => esc(c.before)], ["After", (c) => esc(c.after)],
    ["Why disputed", (c) => esc(c.why_disputed)], ["Re-verify", (c) => esc(c.after_verdict)]], changes);
}

function renderDownloads() {
  const files = [["results.csv", "Results (CSV)"], ["results.json", "Results (JSON)"], ["summary.json", "Summary"], ["changes.json", "Changes"], ["human_check.csv", "Human-check sheet"]];
  $("#downloads").innerHTML = files.map(([f, l]) => `<a class="chip" href="${BASE}${f}" download>${l}</a>`).join("");
  $("#repo-link").href = window.REPO_URL;
}

function initResults() {
  $("#new-run").addEventListener("click", () => { location.hash = ""; location.reload(); });
  $("#drawer-close").addEventListener("click", () => ($("#drawer").hidden = true));
  $("#drawer").addEventListener("click", (e) => e.target.id === "drawer" && ($("#drawer").hidden = true));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#drawer").hidden = true; $("#prompt-modal").hidden = true; } });
  $("#hc-input").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f || !RUN_ID) return;
    const msg = $("#hc-msg");
    msg.textContent = "Uploading and recomputing accuracy…";
    msg.style.color = "";
    const fd = new FormData();
    fd.append("file", f);
    try {
      await getJSON(`api/runs/${RUN_ID}/human-check`, { method: "POST", body: fd });
      await openResults(BASE, RUN_ID);
      document.getElementById("verification").scrollIntoView();
      $("#hc-msg").textContent = "Accuracy updated.";
      $("#hc-msg").style.color = "var(--ok)";
    } catch (err) {
      msg.textContent = err.message;
      msg.style.color = "var(--bad)";
    }
    e.target.value = "";
  });
}

// ---------------------------------------------------------------- start
async function main() {
  initUpload();
  initResults();
  let health = null;
  try { health = await getJSON("api/health"); } catch { /* static hosting */ }
  if (!health) {  // no backend: show saved results if this folder has them
    try { await getJSON("data/summary.json"); return openResults("data/", null); } catch { show("upload"); return; }
  }
  if (health.mock) $("#mock-banner").hidden = false;
  $("#max-rows").textContent = health.max_rows;
  loadPrompts();
  const hashId = (location.hash.match(/run=([\w-]+)/) || [])[1];
  const id = hashId || health.current;
  if (id) {
    try {
      const s = await getJSON(`api/runs/${id}/status?since=999999`);
      if (s.state === "done" && hashId) return openResults(`api/runs/${id}/data/`, id);
      if (s.state === "running" || s.state === "queued") return watchRun(id);
    } catch {
      // server restarted: the run's files may still be on disk, so try to open its results directly
      if (hashId) { try { await getJSON(`api/runs/${hashId}/data/summary.json`); return openResults(`api/runs/${hashId}/data/`, hashId); } catch { /* gone */ } }
    }
  }
  show("upload");
}

main();

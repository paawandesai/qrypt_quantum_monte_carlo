// Monte Carlo console: static, no framework, no third-party code or analytics.
// The API key lives in sessionStorage for this tab only and is sent only as an
// Authorization header to this same origin. It never goes in a URL or a log.
"use strict";

const KEY_STORE = "qmc.apiKey";
const FINAL = new Set(["COMPLETE", "PARTIAL"]);
const STANDARD = { S0: 100, K: 100, r: 0.05, sigma: 0.2, T: 1, paths: 1000000, shards: 10 };
const HEX_FIELDS = ["estimate", "std_error", "ci_low", "ci_high"];
let pollTimer = null;
let systemInfo = null;
let afterConnect = "#/jobs";

// ---- small DOM helper: builds nodes, inserts data only as text ---------------
function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const view = () => document.getElementById("view");
function render(...nodes) { view().replaceChildren(...nodes.filter((n) => n !== null && n !== undefined)); view().focus({ preventScroll: true }); }
function badge(status) { return el("span", { class: `badge s-${status}` }, status); }
function fmt(n, d = 4) { return n === null || n === undefined ? "—" : Number(n).toFixed(d); }
function when(iso) { return iso ? new Date(iso).toLocaleString() : "—"; }
function dur(a, b) {
  if (!a || !b) return "—";
  const s = (new Date(b) - new Date(a)) / 1000;
  return s < 120 ? `${s.toFixed(1)} s` : `${(s / 60).toFixed(1)} min`;
}

// ---- API ----------------------------------------------------------------------
const getKey = () => { try { return sessionStorage.getItem(KEY_STORE); } catch { return null; } };
function setKey(k) { try { k ? sessionStorage.setItem(KEY_STORE, k) : sessionStorage.removeItem(KEY_STORE); } catch { /* private mode */ } }

async function api(method, path, body, opts = {}) {
  const t0 = performance.now();
  let resp;
  try {
    resp = await fetch(path, {
      method,
      headers: { Authorization: `Bearer ${getKey()}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return { status: 0, data: { error: "network", message: "Could not reach the API. Check your connection." }, ms: 0 };
  }
  const ms = Math.round(performance.now() - t0);
  let data = {};
  try { data = await resp.json(); } catch { data = {}; }
  if (resp.status === 401 && !opts.noRedirect) { setKey(null); location.hash = "#/connect"; }
  return { status: resp.status, data, headers: resp.headers, ms };
}

function errorBox(res) {
  const d = res.data || {};
  return el("div", { class: "alert", role: "alert" },
    el("strong", {}, `${res.status || "Network"} ${d.error || "error"}`), el("br"),
    d.message || "Request failed.",
    d.cause ? el("div", {}, "Cause: ", el("code", {}, d.cause), d.qrypt_status ? ` (Qrypt HTTP ${d.qrypt_status})` : "") : null,
    d.job_created === false ? el("div", {}, "No job was created and no seed was generated locally.") : null,
    Array.isArray(d.problems) ? el("ul", {}, d.problems.map((p) => el("li", {}, p))) : null);
}

// ---- Black-Scholes (client preview; the server recomputes) --------------------
function normCdf(x) {
  // Abramowitz-Stegun 7.1.26 erf approximation, |error| < 1.5e-7: display only.
  const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(x * x) / 2);
  return x >= 0 ? (1 + y) / 2 : (1 - y) / 2;
}
function bsCall(S0, K, r, sigma, T) {
  if (!(S0 > 0 && K > 0 && sigma > 0 && T > 0)) return null;
  const d1 = (Math.log(S0 / K) + (r + sigma * sigma / 2) * T) / (sigma * Math.sqrt(T));
  return S0 * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d1 - sigma * Math.sqrt(T));
}

// ---- views ----------------------------------------------------------------------
function viewConnect(message) {
  const input = el("input", { id: "key", type: "password", autocomplete: "off", required: true, "aria-describedby": "key-help" });
  const out = el("div");
  const form = el("form", {
    class: "panel", onsubmit: async (e) => {
      e.preventDefault();
      setKey(input.value.trim());
      input.value = "";
      const res = await api("GET", "/health", undefined, { noRedirect: true });
      // A 503 from /health means the key was accepted but a dependency is degraded;
      // connect anyway so the System view can show what is wrong.
      const accepted = res.status === 200 || (res.status === 503 && res.data?.error !== "api_key_unavailable");
      if (accepted) { location.hash = afterConnect; }
      else { setKey(null); out.replaceChildren(errorBox(res)); }
    },
  },
  el("label", { for: "key" }, "API key", input),
  el("p", { id: "key-help", class: "muted" },
    "Paste the bearer key. It is kept in this browser tab only (sessionStorage) and cleared when you disconnect or close the tab."),
  el("button", { type: "submit" }, "Connect"), out);
  render(el("h1", {}, "Connect"), message ? el("div", { class: "alert info" }, message) : null, form);
}

async function viewJobs() {
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const status = q.get("status") || "";
  const select = el("select", { id: "status", onchange: (e) => { location.hash = e.target.value ? `#/jobs?status=${e.target.value}` : "#/jobs"; } },
    ["", "QUEUED", "RUNNING", "AGGREGATING", "COMPLETE", "PARTIAL"].map((s) => el("option", { value: s, selected: s === status }, s || "All statuses")));
  const find = el("input", { id: "find", placeholder: "32-character job id", autocomplete: "off" });
  const toolbar = el("div", { class: "row panel" },
    el("label", { for: "status" }, "Status", select),
    el("form", { class: "row", onsubmit: (e) => { e.preventDefault(); const id = find.value.trim().toLowerCase(); if (id) location.hash = `#/job/${id}`; } },
      el("label", { for: "find" }, "Find job", find), el("button", { type: "submit", class: "secondary" }, "Open")));
  render(el("h1", {}, "Jobs"), toolbar, el("p", { class: "muted" }, "Loading…"));
  const res = await api("GET", `/jobs${status ? `?status=${status}` : ""}`);
  if (res.status !== 200) return render(el("h1", {}, "Jobs"), toolbar, errorBox(res));
  const jobs = res.data.jobs || [];
  if (!jobs.length) {
    return render(el("h1", {}, "Jobs"), toolbar, el("div", { class: "panel" },
      el("p", {}, status ? `No ${status} jobs.` : "No jobs yet."),
      el("a", { href: "#/new" }, "Run the standard test case (S0 100, K 100, r 0.05, σ 0.2, T 1, 1M paths)")));
  }
  const rows = jobs.map((j) => el("tr", {},
    el("td", {}, el("a", { href: `#/job/${j.job_id}`, class: "mono" }, j.job_id.slice(0, 12) + "…")),
    el("td", {}, badge(j.status), j.replay_of ? el("span", { class: "muted" }, " replay") : null),
    el("td", {}, when(j.created_at)),
    el("td", { class: "num" }, Number(j.paths).toLocaleString()),
    el("td", { class: "num" }, j.shards),
    el("td", { class: "num" }, j.estimate ? fmt(j.estimate) : "—")));
  render(el("h1", {}, "Jobs"), toolbar, el("div", { class: "panel table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "Job"), el("th", {}, "Status"), el("th", {}, "Created"),
      el("th", { class: "num" }, "Paths"), el("th", { class: "num" }, "Shards"), el("th", { class: "num" }, "Estimate"))),
    el("tbody", {}, rows))));
}

async function viewNew() {
  if (!systemInfo) { const s = await api("GET", "/system"); if (s.status === 200) systemInfo = s.data; }
  const lim = (systemInfo && systemInfo.limits) || { max_paths: 10000000, max_shards: 100, max_paths_per_shard: 2000000, fault_injection: false };
  const ent = systemInfo && systemInfo.entropy;
  const fields = [["S0", "Spot S0", "any"], ["K", "Strike K", "any"], ["r", "Rate r", "any"], ["sigma", "Volatility σ", "any"], ["T", "Maturity T (years)", "any"], ["paths", "Paths", "1"], ["shards", "Shards", "1"]];
  const inputs = {};
  const errs = {};
  const grid = el("div", { class: "grid-form" }, fields.map(([k, label, step]) => {
    inputs[k] = el("input", { id: `f-${k}`, name: k, type: "number", step, value: STANDARD[k], required: true, "aria-describedby": `e-${k}` });
    errs[k] = el("div", { id: `e-${k}`, class: "field-error" });
    return el("label", { for: `f-${k}` }, label, inputs[k], errs[k]);
  }));
  let faultInput = null;
  if (lim.fault_injection) {
    faultInput = el("input", { id: "f-fail", placeholder: "e.g. 2, 5", autocomplete: "off" });
    grid.append(el("label", { for: "f-fail" }, "Inject failure in shards (test only)", faultInput));
  }
  const preview = el("p", { class: "preview", "aria-live": "polite" });
  const submit = el("button", { type: "submit" }, "Submit job");
  const out = el("div");
  const read = () => Object.fromEntries(Object.keys(inputs).map((k) => [k, Number(inputs[k].value)]));
  function validate() {
    const v = read();
    const p = {};
    for (const k of ["S0", "K", "sigma", "T"]) if (!(v[k] > 0)) p[k] = `${k} must be greater than 0`;
    if (!(v.r >= -1 && v.r <= 1)) p.r = "r must be between -1 and 1";
    if (!Number.isInteger(v.paths) || v.paths < 2) p.paths = "paths must be a whole number ≥ 2";
    else if (v.paths > lim.max_paths) p.paths = `paths must be ≤ ${lim.max_paths.toLocaleString()} (cost limit)`;
    if (!Number.isInteger(v.shards) || v.shards < 1) p.shards = "shards must be a whole number ≥ 1";
    else if (v.shards > lim.max_shards) p.shards = `shards must be ≤ ${lim.max_shards}`;
    else if (v.shards > v.paths) p.shards = "shards must be ≤ paths";
    else if (Math.ceil(v.paths / v.shards) > lim.max_paths_per_shard) p.shards = `paths per shard must be ≤ ${lim.max_paths_per_shard.toLocaleString()}; use more shards`;
    for (const k of Object.keys(inputs)) { errs[k].textContent = p[k] || ""; inputs[k].setAttribute("aria-invalid", p[k] ? "true" : "false"); }
    const bs = bsCall(v.S0, v.K, v.r, v.sigma, v.T);
    const ceilingHit = ent && ent.kib_used + 1 > ent.ceiling_kib;
    preview.textContent = `≈ ${v.shards > 0 ? Math.ceil(v.paths / v.shards).toLocaleString() : "—"} paths per shard · Black-Scholes reference ${bs === null ? "—" : bs.toFixed(4)} · entropy cost 1 KiB` +
      (ent ? ` (${ent.kib_used.toLocaleString()} of ${ent.ceiling_kib.toLocaleString()} KiB used)` : "") +
      (ceilingHit ? " · entropy ceiling reached: submit is disabled" : "");
    submit.disabled = Object.keys(p).length > 0 || ceilingHit;
  }
  grid.addEventListener("input", validate);
  const form = el("form", {
    class: "panel", onsubmit: async (e) => {
      e.preventDefault();
      validate();
      if (submit.disabled) return;
      const body = read();
      if (faultInput && faultInput.value.trim()) body.fail_shards = faultInput.value.split(/[\s,]+/).filter(Boolean).map(Number);
      submit.disabled = true;
      submit.textContent = "Fetching Qrypt entropy…";
      const res = await api("POST", "/jobs", body);
      submit.textContent = "Submit job";
      if (res.status === 202) { location.hash = `#/job/${res.data.job_id}`; return; }
      out.replaceChildren(errorBox(res));
      if (res.status === 429) countdown(Number(res.headers.get("Retry-After") || 10));
      else validate();
    },
  }, grid, preview, el("div", { class: "row" }, submit, el("span", { class: "muted" }, "One Qrypt request per job. If Qrypt is unavailable the job is rejected.")), out);
  function countdown(s) {
    submit.disabled = true;
    const tick = () => {
      if (s <= 0) { submit.textContent = "Submit job"; validate(); return; }
      submit.textContent = `Rate limited, retry in ${s}s`; s -= 1; setTimeout(tick, 1000);
    };
    tick();
  }
  render(el("h1", {}, "New job"), form);
  validate();
}

function resultCard(job) {
  const r = job.result;
  if (!r) return null;
  const z = r.z_vs_black_scholes === null ? null : Number(r.z_vs_black_scholes);
  const pass = z !== null && Math.abs(z) < 3;
  return el("div", { class: "panel" },
    el("h2", {}, "Result"),
    el("div", { class: "big" }, fmt(r.estimate, 6)),
    el("dl", { class: "kv" },
      el("dt", {}, "Standard error"), el("dd", {}, fmt(r.std_error, 6)),
      el("dt", {}, "95% interval"), el("dd", {}, `[${fmt(r.ci_low, 6)}, ${fmt(r.ci_high, 6)}]`),
      el("dt", {}, "Paths"), el("dd", {}, Number(r.n).toLocaleString()),
      el("dt", {}, "Black-Scholes"), el("dd", {}, fmt(r.black_scholes, 6)),
      el("dt", {}, "Distance"), el("dd", {}, z === null ? "—" : `${z >= 0 ? "+" : ""}${z.toFixed(3)} SE `,
        z === null ? null : el("span", { class: pass ? "pass" : "fail" }, pass ? "✓ within 3 SE" : "✗ outside 3 SE"))));
}

function shardGrid(job) {
  const p = job.progress;
  const done = new Set(p.done_indexes);
  const failed = new Set(p.failed_indexes);
  const errors = job.shard_errors || {};
  const cells = [];
  for (let i = 0; i < p.shards; i++) {
    const state = failed.has(i) ? "failed" : done.has(i) ? "done" : "pending";
    const label = state === "failed" ? `✗${i}` : state === "done" ? `✓${i}` : `${i}`;
    cells.push(el("div", { class: `shard ${state}`, title: `shard ${i}: ${state}${errors[i] ? ` (${errors[i]})` : ""}` }, label));
  }
  return el("div", { class: "panel" },
    el("h2", {}, `Shards: ${p.done} of ${p.shards} done${p.failed ? `, ${p.failed} failed` : ""}`),
    el("div", { class: "shards", role: "list" }, cells),
    job.failed_shard_indexes ? el("p", {}, "Failed shard indexes: ", el("code", {}, job.failed_shard_indexes.join(", ")),
      Object.keys(errors).length ? el("span", { class: "muted" }, ` · errors: ${Object.entries(errors).map(([i, c]) => `${i}=${c}`).join(", ")}`) : null) : null);
}

function provenance(job) {
  const e = job.entropy || {};
  const rt = job.runtime_expected || {};
  return el("details", { class: "panel" },
    el("summary", {}, el("strong", {}, "Provenance")),
    el("p", { class: "muted" }, "Recorded seed provenance: the master seed is SHA-256 over a domain tag and one 1 KiB Qrypt block, and the job stores that block's SHA-256. The block itself is not kept and Qrypt issues no receipt, so this records what was used; it cannot prove where the block came from."),
    el("dl", { class: "kv" },
      el("dt", {}, "Entropy source"), el("dd", {}, job.entropy_source || "—"),
      el("dt", {}, "Qrypt request id"), el("dd", { class: "mono" }, e.qrypt_request_id || "not provided by Qrypt"),
      el("dt", {}, "Master seed"), el("dd", { class: "mono" }, job.master_seed_hex),
      el("dt", {}, "SHA-256 of Qrypt block"), el("dd", { class: "mono" }, e.block_sha256 || "—"),
      el("dt", {}, "Fetched"), el("dd", {}, when(e.fetched_at)),
      el("dt", {}, "Entropy used"), el("dd", {}, `${e.kib ?? "—"} KiB`),
      el("dt", {}, "Shard seed derivation"), el("dd", { class: "mono" }, e.derivation || "—"),
      el("dt", {}, "Runtime"), el("dd", { class: "mono" }, `numpy ${rt.numpy_version} · ${rt.arch} · ${rt.algo_version}`),
      el("dt", {}, "Shard hosts"), el("dd", {}, job.runtime_consistent === undefined ? "—" : job.runtime_consistent ? "identical runtime" : (job.runtime_mismatch_fields || []).length ? `runtime differs: ${job.runtime_mismatch_fields.join(", ")}` : "same runtime, mixed CPU feature sets (does not affect replay)")),
    el("div", { class: "row" }, el("button", { type: "button", class: "secondary", onclick: () => downloadBundle(job.job_id) }, "Download job bundle"),
      el("span", { class: "muted" }, "Recompute offline: python scripts/verify.py bundle.json")));
}

function replayComparison(job) {
  const c = job.replay_comparison;
  if (!c) return null;
  const ok = c.verdict === "bit-identical";
  return el("div", { class: "panel" },
    el("h2", {}, "Replay comparison (float64 hex)"),
    el("p", {}, "Verdict: ", el("span", { class: ok ? "pass" : "fail" }, ok ? "✓ bit-identical" : "✗ different")),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Field"), el("th", {}, "Original"), el("th", {}, "Replay"), el("th", {}, "Match"))),
      el("tbody", {}, HEX_FIELDS.map((f) => {
        const row = c.fields[f] || {};
        return el("tr", {}, el("td", {}, f), el("td", { class: "mono" }, row.original || "—"), el("td", { class: "mono" }, row.replay || "—"),
          el("td", {}, row.equal ? el("span", { class: "pass" }, "✓ equal") : el("span", { class: "fail" }, "✗ different")));
      })))));
}

async function downloadBundle(id) {
  const res = await api("GET", `/jobs/${id}/bundle`);
  if (res.status !== 200) return alert(`${res.status} ${res.data.error || "error"}`);
  const url = URL.createObjectURL(new Blob([JSON.stringify(res.data, null, 2)], { type: "application/json" }));
  const a = el("a", { href: url, download: `qmc-${id}.json` });
  document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

async function viewJob(id) {
  const res = await api("GET", `/jobs/${id}`);
  if (res.status !== 200) {
    return render(el("h1", {}, "Job"), errorBox(res), el("a", { href: "#/jobs" }, "Back to jobs"));
  }
  const job = res.data;
  const final = FINAL.has(job.status);
  const share = `${location.origin}/#/job/${job.job_id}`;
  const actions = el("div", { class: "row" },
    el("button", { type: "button", class: "secondary", onclick: () => navigator.clipboard && navigator.clipboard.writeText(job.job_id) }, "Copy id"),
    el("button", { type: "button", class: "secondary", onclick: () => navigator.clipboard && navigator.clipboard.writeText(share) }, "Copy link"),
    job.status === "COMPLETE" ? el("button", {
      type: "button", onclick: async (e) => {
        if (!confirm("Replay re-runs this job from its stored seed and parameters. It uses no new entropy. Continue?")) return;
        e.target.disabled = true;
        const r = await api("POST", `/jobs/${job.job_id}/replay`);
        if (r.status === 202) location.hash = `#/job/${r.data.job_id}`;
        else { e.target.disabled = false; view().prepend(errorBox(r)); }
      },
    }, "Replay") : null);
  const p = job.params;
  render(
    el("h1", {}, "Job ", el("span", { class: "mono" }, job.job_id)),
    el("div", { class: "panel" },
      el("div", { class: "row" }, badge(job.status), job.replay_of ? el("span", {}, "Replay of ", el("a", { href: `#/job/${job.replay_of}`, class: "mono" }, job.replay_of)) : null),
      el("dl", { class: "kv" },
        el("dt", {}, "Created"), el("dd", {}, when(job.created_at)),
        el("dt", {}, "Duration"), el("dd", {}, final ? dur(job.created_at, job.completed_at) : `running for ${dur(job.created_at, new Date().toISOString())}`),
        el("dt", {}, "Parameters"), el("dd", {}, `S0 ${p.S0} · K ${p.K} · r ${p.r} · σ ${p.sigma} · T ${p.T} · ${Number(p.paths).toLocaleString()} paths · ${p.shards} shards`)),
      actions),
    job.status === "PARTIAL" ? el("div", { class: "alert warn", role: "alert" }, "This job is PARTIAL: some shards failed or never finished, so no price is reported.") : null,
    resultCard(job), replayComparison(job), shardGrid(job), provenance(job));
  if (!final) pollTimer = setTimeout(() => { if (location.hash === `#/job/${id}`) viewJob(id); }, 2000);
}

async function viewSystem() {
  const res = await api("GET", "/system");
  const health = await api("GET", "/health");
  if (res.status !== 200) return render(el("h1", {}, "System"), errorBox(res));
  systemInfo = res.data;
  const s = res.data;
  const h = health.data || {};
  const ls = h.last_success || {};
  render(el("h1", {}, "System"),
    el("div", { class: "panel" }, el("h2", {}, "Health"),
      el("dl", { class: "kv" },
        el("dt", {}, "Status"), el("dd", {}, h.status || "—"),
        ...Object.entries(h.tables || {}).flatMap(([t, st]) => [el("dt", {}, t), el("dd", {}, st)]),
        el("dt", {}, "Last Qrypt fetch"), el("dd", {}, when(ls.qrypt_fetch_at)),
        el("dt", {}, "Last key load"), el("dd", {}, when(ls.api_key_loaded_at)),
        el("dt", {}, "Last job created"), el("dd", {}, when(ls.job_created_at))),
      el("p", { class: "muted" }, h.scope || "")),
    el("div", { class: "panel" }, el("h2", {}, "Entropy and limits"),
      el("dl", { class: "kv" },
        el("dt", {}, "Entropy used"), el("dd", {}, `${s.entropy.kib_used.toLocaleString()} of ${s.entropy.ceiling_kib.toLocaleString()} KiB`),
        el("dt", {}, "Qrypt rate limit"), el("dd", {}, `${s.qrypt_rate_limit.requests} requests per ${s.qrypt_rate_limit.window_s} s`),
        el("dt", {}, "Max paths per job"), el("dd", {}, s.limits.max_paths.toLocaleString()),
        el("dt", {}, "Max shards"), el("dd", {}, s.limits.max_shards),
        el("dt", {}, "Max paths per shard"), el("dd", {}, s.limits.max_paths_per_shard.toLocaleString()),
        el("dt", {}, "Fault injection"), el("dd", {}, s.limits.fault_injection ? "enabled (test path)" : "off"),
        el("dt", {}, "DLQ depth"), el("dd", {}, s.dlq_depth === null ? "—" : String(s.dlq_depth)),
        el("dt", {}, "Runtime"), el("dd", { class: "mono" }, `numpy ${s.runtime.numpy_version} · ${s.runtime.arch}`),
        el("dt", {}, "Auth"), el("dd", {}, s.auth))),
    el("p", { class: "muted" }, "Spend and alarms live in the AWS console (Budgets, CloudWatch dashboard); this console does not read billing data."));
}

// ---- router -------------------------------------------------------------------
async function route() {
  clearTimeout(pollTimer);
  const hash = location.hash || "#/jobs";
  const connected = !!getKey();
  document.getElementById("nav").hidden = !connected;
  const name = hash.slice(2).split(/[/?]/)[0];
  for (const a of document.querySelectorAll("[data-nav]")) a.classList.toggle("active", a.dataset.nav === name);
  if (!connected) {
    if (!hash.startsWith("#/connect")) afterConnect = hash;
    return viewConnect(hash.startsWith("#/connect") ? null : "Connect with the API key to continue.");
  }
  if (name === "connect") return viewConnect();
  if (name === "new") return viewNew();
  if (name === "system") return viewSystem();
  if (name === "job") return viewJob(hash.split("/")[2]);
  return viewJobs();
}

document.getElementById("disconnect").addEventListener("click", () => { setKey(null); location.hash = "#/connect"; });
window.addEventListener("hashchange", route);
route();

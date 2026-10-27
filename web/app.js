"use strict";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
let py = null;
let api = null;
let current = null;      // scenario id
let coverageCutoff = 1e18;

const SCENARIOS = [
  { id: "expanding", account: "Acme Robotics", title: "Expanding organization",
    blurb: "Spend, people and products all moving at once. The easy case: strong, trusted and actionable.",
    v: { account_age_days: 120, consumption_30d: 18000, velocity: 2.6, users_total: 14, users_added_30d: 6,
         top_user_share: 0.25, features_total: 4, features_added_30d: 2, open_opportunity: false,
         ownership: "resolved", activity: "none" } },
  { id: "whale", account: "Northstar Labs", title: "Consumption whale",
    blurb: "Large, fast-growing usage, almost all from one person. Strong on value, weak as evidence of an organization buying in.",
    v: { account_age_days: 150, consumption_30d: 42000, velocity: 3.4, users_total: 3, users_added_30d: 0,
         top_user_share: 0.94, features_total: 1, features_added_30d: 0, open_opportunity: false,
         ownership: "resolved", activity: "none" } },
  { id: "early", account: "Meridian Health", title: "Early organizational adoption",
    blurb: "Five weeks old. Too new for spend velocity to mean anything, but seven people joined after signup week.",
    v: { account_age_days: 34, consumption_30d: 2600, velocity: 4.0, users_total: 10, users_added_30d: 7,
         top_user_share: 0.3, features_total: 2, features_added_30d: 1, open_opportunity: false,
         ownership: "resolved", activity: "none" } },
  { id: "engaged", account: "Atlas Software", title: "Already engaged",
    blurb: "Every product signal is strong. A seller is already working an opportunity, so another alert would only add noise.",
    v: { account_age_days: 200, consumption_30d: 26000, velocity: 2.4, users_total: 22, users_added_30d: 5,
         top_user_share: 0.2, features_total: 5, features_added_30d: 2, open_opportunity: true,
         ownership: "resolved", activity: "meeting", activity_days_ago: 3 } },
  { id: "unowned", account: "Harbor Logistics", title: "Ownership unresolved",
    blurb: "Strong signal on an account whose CRM owner left the company. Nobody can act until routing is fixed.",
    v: { account_age_days: 140, consumption_30d: 15000, velocity: 2.2, users_total: 9, users_added_30d: 4,
         top_user_share: 0.35, features_total: 3, features_added_30d: 2, open_opportunity: false,
         ownership: "owner_inactive", activity: "none" } },
  { id: "fn", account: "Pinecrest Analytics", title: "False negative",
    blurb: "One of the largest accounts in the book, flat month over month. Signals detect change, so this never fires. The coverage audit is what catches it.",
    v: { account_age_days: 400, consumption_30d: 65000, velocity: 1.03, users_total: 40, users_added_30d: 1,
         top_user_share: 0.18, features_total: 3, features_added_30d: 0, open_opportunity: false,
         ownership: "resolved", activity: "none" } },
];
const DEFAULTS = { largest_event_share: 0.05, data_lag_days: 0, activity_days_ago: 5, ai: true };

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (n) => Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

function loadScript(src) {
  return new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = () => fail(new Error("could not load " + src));
    document.head.appendChild(s);
  });
}

async function boot() {
  renderScenarios();
  loadReport();
  try {
    await loadScript(PYODIDE + "pyodide.js");
    py = await loadPyodide({ indexURL: PYODIDE });
    const manifest = await (await fetch("py_manifest.json")).json();
    for (const f of manifest.files) {
      const text = await (await fetch("../" + f)).text();
      const dir = "/home/pyodide/" + f.split("/").slice(0, -1).join("/");
      py.FS.mkdirTree(dir);
      py.FS.writeFile("/home/pyodide/" + f, text);
    }
    py.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
    api = py.pyimport("expansion_signals.demo_api");
    $("#mode").textContent = "engine loaded";
    $("#mode").classList.add("ok");
    $("#evaluate").disabled = false;
    pick(SCENARIOS[0].id, true);
  } catch (e) {
    $("#mode").textContent = "engine failed to load";
    $("#mode").classList.add("bad");
    $("#inspector").innerHTML = `<p class="bad-text">${esc(e.message)}</p><p class="muted">The demo loads Pyodide from a CDN. Run it locally with <code>make demo</code>.</p>`;
  }
}

function renderScenarios() {
  $("#scenarios").innerHTML = SCENARIOS.map((s, i) =>
    `<button class="scn" data-id="${s.id}"><span class="n">${i + 1}</span><span><b>${esc(s.title)}</b><br><span class="muted small">${esc(s.account)}</span></span></button>`).join("");
  $("#scenarios").addEventListener("click", (e) => {
    const b = e.target.closest(".scn");
    if (b) pick(b.dataset.id, true);
  });
}

function scenario() { return SCENARIOS.find((s) => s.id === current); }

function pick(id, evaluateNow) {
  current = id;
  const s = scenario();
  document.querySelectorAll(".scn").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
  $("#acct-name").textContent = s.account;
  $("#acct-blurb").textContent = s.blurb;
  const vals = { ...DEFAULTS, ...s.v };
  const form = $("#form");
  for (const [k, v] of Object.entries(vals)) {
    const el = form.elements[k];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
  }
  if (api) { api.reset(s.account); if (evaluateNow) evaluate(); }
}

function readForm() {
  const f = $("#form").elements;
  const num = (n) => Number(f[n].value);
  return {
    account: scenario().account,
    account_age_days: num("account_age_days"), consumption_30d: num("consumption_30d"), velocity: num("velocity"),
    users_total: num("users_total"), users_added_30d: num("users_added_30d"), top_user_share: num("top_user_share"),
    features_total: num("features_total"), features_added_30d: num("features_added_30d"),
    largest_event_share: num("largest_event_share"), data_lag_days: num("data_lag_days"),
    activity: f.activity.value, activity_days_ago: num("activity_days_ago"),
    open_opportunity: f.open_opportunity.checked, ownership: f.ownership.value, ai: f.ai.checked,
    coverage_cutoff: coverageCutoff,
  };
}

function evaluate() {
  if (!api) return;
  const r = JSON.parse(api.run(JSON.stringify(readForm())));
  renderInspector(r);
}

const DECISION_TEXT = {
  SURFACE: "Put in front of the owning seller",
  MONITOR: "Real but weak: keep watching, spend no seller time",
  HOLD: "Strong, but not actionable today",
  SUPPRESS: "Strong, but someone is already on it, or the seller said no",
  ROUTE: "Strong, but nobody can act until ownership or identity is fixed",
  NO_SIGNAL: "No expansion behavior detected",
};
const tone = (d) => ({ SURFACE: "ok", MONITOR: "idle", HOLD: "warn", SUPPRESS: "idle", ROUTE: "warn", NO_SIGNAL: "idle" }[d]);
const bandTone = (b) => ({ HIGH: "ok", MEDIUM: "warn", LOW: "bad", NONE: "idle" }[b]);

function renderInspector(r) {
  $("#acct-day").textContent = r.days_advanced ? `${r.today} (+${r.days_advanced}d)` : r.today;
  $("#insp-version").textContent = `logic ${r.logic_version}`;
  const fam = r.families.map((f) => `
    <div class="fam ${f.fired ? "on" : ""}">
      <div class="fam-h"><span class="dot ${f.fired ? "ok" : "idle"}"></span><b>${esc(famName(f.family))}</b>
        <span class="tag ${bandTone(f.band)}">${f.fired ? f.band : "not fired"}</span></div>
      <ul>${f.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>
    </div>`).join("");
  const passed = r.rules_passed.map((x) => `<li class="pass">${esc(x)}</li>`).join("");
  const blocked = r.blocking_reasons.map((x) => `<li class="fail">${esc(x)}</li>`).join("");
  const crm = r.crm;
  const ai = r.ai_context || {};
  const aiHtml = ai.used
    ? `<p><span class="tag ok">used</span> ${esc(ai.scope)}</p>
       <p class="muted small">Fields sent: ${ai.fields_sent.map((x) => `<code>${esc(x)}</code>`).join(" ")}. No raw events, no user identities. The decision above was final before this ran.</p>
       <blockquote>${esc(ai.text)}</blockquote>`
    : `<p><span class="tag idle">not used</span> ${esc(ai.reason || "")}</p>`;
  const sig = r.signal;
  const coverage = r.coverage_flag
    ? `<div class="callout warn"><b>Coverage audit would list this account.</b> It is in the top 10% by 30-day consumption, no signal fired, and no seller is talking to it. The audit is a weekly review for RevOps, not a seller alert.</div>` : "";

  $("#inspector").className = "";
  $("#inspector").innerHTML = `
    <div class="decision ${tone(r.decision)}">
      <div class="d-label">Decision</div>
      <div class="d-value">${r.decision}</div>
      <div class="d-text">${esc(DECISION_TEXT[r.decision])}</div>
    </div>
    <div class="trio">
      <div><div class="k">Signal strength</div><div class="v tag ${bandTone(r.strength)}">${r.strength}</div><div class="muted small">how much expansion behavior</div></div>
      <div><div class="k">Confidence</div><div class="v tag ${bandTone(r.confidence)}">${r.confidence}</div><div class="muted small">how far to trust the reading</div></div>
      <div><div class="k">Actionable</div><div class="v tag ${r.actionable ? "ok" : "idle"}">${r.actionable ? "YES" : "NO"}</div><div class="muted small">can this owner act today</div></div>
    </div>
    ${coverage}
    <h3>Product evidence</h3><p class="muted small">${esc(r.strength_reason)}</p>${fam}
    <h3>Confidence</h3><ul>${r.confidence_reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    <h3>CRM context</h3>
    <table class="kv">
      <tr><td>Identity</td><td>${crm.identity.length ? crm.identity.map(esc).join(", ") : "no match"} <span class="muted">(${esc(crm.match_method)})</span></td></tr>
      <tr><td>Owner</td><td>${crm.owner ? esc(crm.owner) + (crm.owner_active ? "" : ' <span class="tag bad">inactive</span>') : "none"}</td></tr>
      <tr><td>Open opportunity</td><td>${crm.open_opportunity ? "yes" : "no"}</td></tr>
      <tr><td>Last activity</td><td>${esc(crm.last_activity || "none")}</td></tr>
    </table>
    ${r.seller_notes.length ? `<p class="small"><b>Note for the seller:</b> ${r.seller_notes.map(esc).join("; ")}</p>` : ""}
    <h3>Eligibility rules</h3>
    <ul class="rules">${blocked}${passed}</ul>
    <h3>AI contribution</h3>${aiHtml}
    <h3>Seller feedback</h3>
    ${feedbackHtml(sig, r.decision)}
  `;
  wireFeedback();
}

function famName(f) {
  return { value: "Usage / value expansion", adoption: "Organizational adoption", breadth: "Product breadth" }[f] || f;
}

function feedbackHtml(sig, decision) {
  const hist = sig ? `<ol class="hist">${sig.history.map(([d, e]) => `<li><span class="mono">${esc(d)}</span> ${esc(e)}</li>`).join("")}</ol>` : "";
  const canRespond = sig && sig.status === "OPEN";
  const buttons = canRespond ? `
    <div class="fb">
      <button class="btn" data-fb="action">Worked it</button>
      <button class="btn" data-fb="snooze">Snooze 14 days</button>
      <select id="fb-reason">
        <option value="not_a_fit">not a fit</option><option value="bad_timing">bad timing</option>
        <option value="already_engaged">already engaged</option><option value="wrong_owner">wrong owner</option>
        <option value="bad_data">bad data</option>
      </select>
      <button class="btn" data-fb="dismiss">Dismiss</button>
    </div>` : "";
  const time = `
    <div class="fb">
      <span class="muted small">Re-evaluate later:</span>
      <button class="btn" data-adv="7">+7 days</button><button class="btn" data-adv="20">+20 days</button><button class="btn" data-adv="95">+95 days</button>
      <button class="btn ghost" data-adv="reset">Reset account</button>
    </div>`;
  const intro = sig ? `<p class="small">Signal <code>${esc(sig.key)}</code>, status <b>${esc(sig.status)}</b>${sig.dismiss_reason ? ` (${esc(sig.dismiss_reason)})` : ""}.</p>`
    : `<p class="muted small">${decision === "SURFACE" ? "" : "No signal was sent, so there is nothing to respond to."}</p>`;
  return intro + hist + buttons + time + `<p class="muted small">Feedback is stored with the signal. The next evaluation reads it: a dismissal suppresses for a reason-specific window unless strength rises, wrong owner routes, bad data holds.</p>`;
}

function wireFeedback() {
  document.querySelectorAll("[data-fb]").forEach((b) => b.addEventListener("click", () => {
    const a = b.dataset.fb;
    api.feedback(scenario().account, a, a === "dismiss" ? $("#fb-reason").value : "");
    evaluateNoRecord();
  }));
  document.querySelectorAll("[data-adv]").forEach((b) => b.addEventListener("click", () => {
    const a = b.dataset.adv;
    if (a === "reset") api.reset(scenario().account); else api.advance(scenario().account, Number(a));
    evaluate();
  }));
}

function evaluateNoRecord() {
  const p = readForm(); p.record = false;
  renderInspector(JSON.parse(api.run(JSON.stringify(p))));
}

async function loadReport() {
  try {
    const r = await (await fetch("../evals/report.json")).json();
    coverageCutoff = r.coverage_audit.consumption_30d_cutoff;
    renderReport(r);
  } catch (e) {
    $("#evaluation").innerHTML = `<p class="muted">Could not load evals/report.json.</p>`;
  }
}

function renderReport(r) {
  const pct = (x) => (x == null ? "n/a" : Math.round(x * 100) + "%");
  const miss = Object.entries(r.miss_causes).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v}</td></tr>`).join("");
  const arch = Object.entries(r.by_archetype).map(([k, v]) =>
    `<tr><td>${esc(k.replace("_", " "))}</td><td class="num">${v.accounts}</td><td class="num">${v.signals_delivered}</td><td class="num">${v.eligible_expansions}</td><td class="num">${v.caught}</td></tr>`).join("");
  const ca = r.coverage_audit;
  $("#evaluation").innerHTML = `
    <h1>Evaluation on synthetic history</h1>
    <p class="muted">A daily replay of ${r.accounts} synthetic accounts from ${r.window[0]} to ${r.window[1]}, with simulated seller responses and two late warehouse days. Outcomes come from the synthetic archetypes, so this shows the measurement working. It does not show that these signals predict expansion anywhere real.</p>
    <div class="stats">
      <div><div class="k">Precision</div><div class="big">${pct(r.precision_60d)}</div><div class="muted small">signals followed by expansion within 60 days</div></div>
      <div><div class="k">Recall</div><div class="big">${pct(r.recall_60d)}</div><div class="muted small">of ${r.eligible_expansions} expansions Sales was not already on</div></div>
      <div><div class="k">Signals delivered</div><div class="big">${r.signals_delivered}</div><div class="muted small">to ${r.accounts_surfaced} distinct accounts</div></div>
      <div><div class="k">Median lead time</div><div class="big">${r.median_lead_time_days ?? "n/a"}<span class="unit"> days</span></div><div class="muted small">first signal to expansion</div></div>
    </div>
    <h2>Why expansions were missed</h2>
    <table class="grid"><thead><tr><th>Cause</th><th class="num">Count</th></tr></thead><tbody>${miss}</tbody></table>
    <div class="callout warn"><b>Coverage audit.</b> ${ca.listed} accounts are in the top 10% by consumption with no signal and no live seller conversation. The list contains ${ca.contains_missed_large_steady} of the ${ca.missed_large_steady} large, steady accounts the signals missed. In this synthetic world that is partly by construction; the point is that the audit needs no outcome labels, so it can run from week one.</div>
    <h2>By archetype</h2>
    <table class="grid"><thead><tr><th>Archetype</th><th class="num">Accounts</th><th class="num">Signals</th><th class="num">Eligible expansions</th><th class="num">Caught</th></tr></thead><tbody>${arch}</tbody></table>
    <h2>Operations</h2>
    <ul>
      <li>Seller responses: ${Object.entries(r.signal_status).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</li>
      <li>Account-days suppressed because a seller had dismissed: ${r.account_days_suppressed_by_dismissal}</li>
      <li>Account-days held because the owner was at weekly capacity: ${r.account_days_held_for_capacity}</li>
      <li>Late warehouse days (${r.late_warehouse_days.join(", ")}): ${r.account_days_held_for_late_data} account-days held instead of decided on partial data</li>
    </ul>
    <p class="muted small">Logic version ${esc(r.logic_version)}. Regenerate with <code>python evals/run_eval.py</code>; CI fails if this report is out of date.</p>`;
}

document.querySelectorAll(".tab[data-view]").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".tab[data-view]").forEach((x) => x.classList.toggle("active", x === t));
  $("#view-evaluate").hidden = t.dataset.view !== "evaluate";
  $("#view-evaluation").hidden = t.dataset.view !== "evaluation";
}));
$("#form").addEventListener("submit", (e) => { e.preventDefault(); evaluate(); });

boot();

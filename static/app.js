/* Shaadi Sorted - single-page UI (vanilla JS, no build step) */
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let S = null;      // server state
let META = null;   // static options
let tab = "home";
let chatMsgs = []; // [{role, text, actions:[]}]
let busy = false;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = {};
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok) {
    let d = data.detail ?? res.statusText;
    if (Array.isArray(d)) d = d.map((e) => `${(e.loc || []).slice(1).join(".")}: ${String(e.msg).replace("Value error, ", "")}`).join("; ");
    throw new Error(d);
  }
  return data;
}

/* ---------- formatting ---------- */
const money = (n) => {
  const cur = S?.currency || "INR";
  return new Intl.NumberFormat(cur === "INR" ? "en-IN" : "en-US", { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(n);
};
const dparts = (iso) => { const d = new Date(iso + "T00:00:00"); return { m: d.toLocaleString("en", { month: "short" }), d: d.getDate(), y: d.getFullYear(), wd: d.toLocaleString("en", { weekday: "short" }), full: d.toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" }) }; };
const daysUntil = (iso) => Math.round((new Date(iso + "T00:00:00") - new Date(new Date().toDateString())) / 86400000);
const sideName = (s) => (s === "both" ? "Both families" : S.couple.side_labels[s]);
const fnName = (id) => (S.functions.find((f) => f.id === id) || {}).name || "";
const num = (n) => n.toLocaleString("en-US");

/* ---------- header ---------- */
function renderHeader() {
  const ready = S.status === "ready";
  $("#meter-total").textContent = num(S.meter.total);
  $("#reset-btn").classList.toggle("hidden", !ready);
  const n = (S.safety_log || []).length;
  $("#safety-btn").classList.toggle("hidden", !ready && n === 0);
  $("#safety-count").textContent = n;
  const u = S.usage;
  const rows = Object.entries(u.by_agent || {}).map(([a, v]) => `<tr><td>${esc(a)}</td><td>${num(v.input)}</td><td>${num(v.cached)}</td><td>${num(v.output)}</td><td>${v.calls}</td></tr>`).join("");
  $("#meter-pop").innerHTML = `
    <h3>Token usage</h3>
    <table><tr><th></th><th>Input</th><th>Cached</th><th>Output</th><th>Calls</th></tr>${rows || '<tr><td colspan="5" class="small">No AI calls yet.</td></tr>'}
    <tr><td><b>Total</b></td><td><b>${num(u.input)}</b></td><td><b>${num(u.cached)}</b></td><td><b>${num(u.output)}</b></td><td><b>${u.calls}</b></td></tr></table>
    <div>Estimated cost: <b>$${S.meter.cost_usd.toFixed(4)}</b></div>
    <div class="small">Model ${esc(META.model)} · cap ${num(S.meter.cap)} tokens. Rates are assumptions (edit app/config.py).</div>
    <div style="margin-top:10px"><button class="btn small light" id="reset-meter">Reset meter</button></div>`;
  $("#reset-meter").onclick = async () => { S = await api("/api/usage/reset", {}); render(); $("#meter-pop").classList.remove("hidden"); };
  const log = (S.safety_log || []).slice().reverse().map((e) => `<li><b>${esc(e.kind.replace(/_/g, " "))}</b> <span class="small muted">${esc(e.time)}</span><br><span class="small">${esc(e.detail)}</span></li>`).join("");
  $("#safety-pop").innerHTML = `<h3>Safety log</h3><div class="small muted">Every time a defense fires it is recorded here.</div><ul style="padding-left:18px">${log || '<li class="small muted">Nothing yet.</li>'}</ul>`;
  const b = $("#banner");
  if (!META.has_key) {
    b.classList.remove("hidden");
    b.innerHTML = "No <b>PORTKEY_API_KEY</b> found. The instant demo works without it; AI planning and the chat agent need the key in <code>.env</code> (see README).";
  } else b.classList.add("hidden");
}

/* ---------- intake ---------- */
function variantOptions(religion, selected) {
  const opts = META.variants.filter((v) => v.applies_to.includes(religion));
  return `<option value="">General (no regional variant)</option>` + opts.map((v) => `<option value="${v.key}" ${v.key === selected ? "selected" : ""}>${esc(v.label)}</option>`).join("");
}
const religionOptions = (sel) => META.religions.map((r) => `<option value="${r.key}" ${r.key === sel ? "selected" : ""}>${esc(r.label)}</option>`).join("");

function renderIntake(prefill = {}) {
  const t1 = (prefill.traditions || [])[0] || { religion: "hindu", variant: "" };
  const t2 = (prefill.traditions || [])[1];
  const soon = new Date(Date.now() + 200 * 86400000).toISOString().slice(0, 10);
  $("#app").innerHTML = `
  <div class="intake">
    <h1>Plan the whole shaadi, in one place.</h1>
    <p class="lead">Tell us about the couple. We build the functions, a dated checklist and a budget split. If two families bring two traditions, we merge both into one plan and flag where they clash.</p>
    <form id="intake" class="card">
      <div class="section-title">The couple</div>
      <div class="grid2">
        <div class="field"><label>Partner 1</label><input name="partner_a" required maxlength="40" value="${esc(prefill.partner_a || "")}" placeholder="e.g. Meera"></div>
        <div class="field"><label>Partner 2</label><input name="partner_b" required maxlength="40" value="${esc(prefill.partner_b || "")}" placeholder="e.g. Aarav"></div>
        <div class="field"><label>Partner 1's family is called</label><input name="side_a_label" maxlength="30" value="${esc(prefill.side_a_label || "Bride's side")}"></div>
        <div class="field"><label>Partner 2's family is called</label><input name="side_b_label" maxlength="30" value="${esc(prefill.side_b_label || "Groom's side")}"></div>
        <div class="field"><label>Partner 1's city</label><input name="city_a" required maxlength="40" value="${esc(prefill.city_a || "")}"></div>
        <div class="field"><label>Partner 2's city</label><input name="city_b" required maxlength="40" value="${esc(prefill.city_b || "")}"></div>
      </div>
      <div class="section-title">The wedding</div>
      <div class="grid3">
        <div class="field"><label>Wedding city</label><input name="wedding_city" required maxlength="40" value="${esc(prefill.wedding_city || "")}"></div>
        <div class="field"><label>Wedding date</label><input type="date" name="wedding_date" required value="${esc(prefill.wedding_date || soon)}"></div>
        <div class="field"><label>Guest count</label><input type="number" name="guest_count" min="10" max="5000" required value="${esc(prefill.guest_count || 200)}"></div>
        <div class="field"><label>Total budget</label><input type="number" name="budget_total" min="1" required value="${esc(prefill.budget_total || "")}" placeholder="2500000"></div>
        <div class="field"><label>Currency</label><select name="currency">${META.currencies.map((c) => `<option ${c === (prefill.currency || "INR") ? "selected" : ""}>${c}</option>`).join("")}</select></div>
      </div>
      <div class="section-title">Tradition</div>
      <div class="grid2">
        <div class="field"><label>Religion</label><select name="religion1" id="rel1">${religionOptions(t1.religion)}</select></div>
        <div class="field"><label>Regional variant</label><select name="variant1" id="var1">${variantOptions(t1.religion, t1.variant)}</select></div>
      </div>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500;margin:6px 0 10px"><input type="checkbox" id="dual" style="width:auto" ${t2 ? "checked" : ""}> Our two families have different traditions</label>
      <div id="trad2" class="grid2 ${t2 ? "" : "hidden"}">
        <div class="field"><label>Second family: religion</label><select name="religion2" id="rel2">${religionOptions((t2 || {}).religion || "sikh")}</select></div>
        <div class="field"><label>Second family: regional variant</label><select name="variant2" id="var2">${variantOptions((t2 || {}).religion || "sikh", (t2 || {}).variant)}</select></div>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn gold" type="submit">Create my plan with AI</button>
        <button class="btn light" type="button" id="sample-btn">Fill in a sample couple</button>
        <button class="btn light" type="button" id="demo-btn">Instant demo (no AI)</button>
      </div>
      <div id="intake-err" class="error"></div>
      <p class="small muted">We never state auspicious dates. Ask your pandit, priest, imam, granthi or monk for those.</p>
    </form>
  </div>`;
  const wire = (relId, varId) => { $(relId).onchange = (e) => { $(varId).innerHTML = variantOptions(e.target.value, ""); }; };
  wire("#rel1", "#var1"); wire("#rel2", "#var2");
  $("#dual").onchange = (e) => $("#trad2").classList.toggle("hidden", !e.target.checked);
  $("#sample-btn").onclick = async () => renderIntake(await api("/api/sample-intake"));
  $("#demo-btn").onclick = () => run("Building the demo plan...", async () => { S = await api("/api/demo", {}); tab = "home"; chatMsgs = []; });
  $("#intake").onsubmit = (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const trads = [{ religion: f.get("religion1"), variant: f.get("variant1") || null }];
    if ($("#dual").checked) trads.push({ religion: f.get("religion2"), variant: f.get("variant2") || null });
    const body = {
      partner_a: f.get("partner_a"), partner_b: f.get("partner_b"), side_a_label: f.get("side_a_label"), side_b_label: f.get("side_b_label"),
      city_a: f.get("city_a"), city_b: f.get("city_b"), wedding_city: f.get("wedding_city"), wedding_date: f.get("wedding_date"),
      guest_count: parseInt(f.get("guest_count"), 10), budget_total: parseFloat(f.get("budget_total")), currency: f.get("currency"),
      traditions: trads, use_llm: true,
    };
    $("#intake-err").textContent = "";
    run(trads.length > 1 ? "Planner agent is picking functions, then the Merge agent combines both traditions..." : "Planner agent is building your plan...",
      async () => { S = await api("/api/intake", body); tab = "home"; chatMsgs = []; }, (m) => { $("#intake-err").textContent = m; });
  };
}

async function run(text, fn, onError) {
  $("#loading-text").textContent = text;
  $("#loading").classList.remove("hidden");
  try { await fn(); render(); }
  catch (e) { if (onError) onError(e.message); else alert(e.message); }
  finally { $("#loading").classList.add("hidden"); }
}

/* ---------- app shell ---------- */
function renderShell() {
  const tabs = [["home", "Home"], ["plan", "Plan"], ["guests", "Guests"]];
  if (S.dual) tabs.push(["two", "Two families, one timeline"]);
  if (!tabs.find((t) => t[0] === tab)) tab = "home";
  $("#app").innerHTML = `
    <div class="layout">
      <section>
        <div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${k === tab ? "active" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>
        <div id="content"></div>
      </section>
      <aside class="card chat" id="chat">
        <div class="chat-head"><h3>Plan assistant</h3><div class="small muted">Edits your plan with tools. Try the examples below.</div></div>
        <div class="chat-body" id="chat-body"></div>
        <div id="confirm-slot"></div>
        <div class="suggest" id="suggest"></div>
        <form class="chat-input" id="chat-form"><input id="chat-text" maxlength="600" placeholder="Add the Sharma family, 4 people, groom side, for the Sangeet" autocomplete="off"><button class="btn" type="submit">Send</button></form>
      </aside>
    </div>`;
  document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; renderShell(); renderContent(); renderChat(); }));
  $("#chat-form").onsubmit = (e) => { e.preventDefault(); const t = $("#chat-text").value.trim(); if (t) { $("#chat-text").value = ""; sendChat(t); } };
}

function renderContent() {
  const el = $("#content");
  if (!el) return;
  el.innerHTML = { home: homeView, plan: planView, guests: guestsView, two: twoView }[tab]();
  wireContent();
}

/* ---------- views ---------- */
function rsvpTotals() {
  return S.functions.map((f) => {
    const c = { yes: 0, maybe: 0, pending: 0, no: 0 };
    S.guests.forEach((g) => { if (g.rsvps[f.id]) c[g.rsvps[f.id]] += g.party_size; });
    return { f, c, total: c.yes + c.maybe + c.pending + c.no };
  });
}

function homeView() {
  const ns = S.next_step;
  const wd = dparts(S.wedding_date);
  const left = daysUntil(S.wedding_date);
  let next;
  if (ns.all_done) next = `<div class="eyebrow">Next step today</div><h2>Everything on the checklist is done.</h2><p class="muted">Enjoy the shaadi!</p>`;
  else {
    const t = ns.task;
    const pill = ns.overdue ? `<span class="pill warn">${-ns.days_left} day${ns.days_left === -1 ? "" : "s"} overdue</span>` : ns.days_left === 0 ? `<span class="pill gold">Due today</span>` : `<span class="pill gold">Due in ${ns.days_left} days</span>`;
    next = `<div class="eyebrow">Next step today</div><h2>${esc(t.title)}</h2>
      <div class="row">${pill}${ns.function ? `<span class="pill">${esc(ns.function)}</span>` : ""}${t.compressed ? `<span class="pill">Rescheduled to fit your timeline</span>` : ""}<span class="muted small">${ns.remaining} tasks left</span></div>
      <div style="margin-top:14px"><button class="btn gold" data-done="${t.id}">Mark done</button></div>`;
  }
  const gen = S.generation.planner;
  const genChip = gen.mode === "ai" ? `<span class="pill ok">Plan written by the Planner agent${S.dual ? " + Merge agent" : ""}</span>` : `<span class="pill">Rules-only plan (no AI call)</span>`;
  const bars = rsvpTotals().map(({ f, c, total }) => {
    const seg = (k, color) => `<span style="width:${total ? (c[k] / total) * 100 : 0}%;background:${color}"></span>`;
    return `<div class="rsvp-row"><div class="head"><b>${esc(f.name)}</b><span class="muted">${total} invited · ${c.yes} yes</span></div>
      <div class="bar">${seg("yes", "var(--yes)")}${seg("maybe", "var(--maybe)")}${seg("pending", "var(--pending)")}${seg("no", "var(--no)")}</div></div>`;
  }).join("");
  const upcoming = S.tasks.filter((t) => !t.done).slice(0, 5).map((t) => `<div class="task"><span class="t">${esc(t.title)}</span><span class="due ${daysUntil(t.due) < 0 ? "over" : ""}">${dparts(t.due).full}</span></div>`).join("");
  const guestsTotal = S.guests.reduce((a, g) => a + g.party_size, 0);
  return `
    <div class="card next-card">${next}</div>
    <div class="stats">
      <div class="card stat"><div class="num">${left}</div><div class="lbl">days to go · ${wd.wd} ${wd.full}</div></div>
      <div class="card stat"><div class="num">${S.functions.length}</div><div class="lbl">functions planned</div></div>
      <div class="card stat"><div class="num">${guestsTotal}</div><div class="lbl">people on guest list (of ~${S.guest_count_estimate})</div></div>
    </div>
    <div class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">RSVPs by function</h2>${genChip}</div><div style="margin-top:12px">${S.guests.length ? bars : '<p class="muted">No guests yet. Ask the assistant to add some.</p>'}</div>
      <div class="legend"><span><i style="background:var(--yes)"></i>Yes</span><span><i style="background:var(--maybe)"></i>Maybe</span><span><i style="background:var(--pending)"></i>Pending</span><span><i style="background:var(--no)"></i>No</span></div></div>
    <div class="card"><h2>Coming up</h2>${upcoming || '<p class="muted">All done.</p>'}</div>`;
}

function planView() {
  const hostLabel = (f) => (f.host_side === "both" ? "Both families" : S.couple.side_labels[f.host_side]);
  const confTag = (f) => {
    if (f.confidence === "high") return "";
    const who = S.traditions_info.flatMap((t) => t.confirm_with.slice(0, 1))[0];
    return `<span class="pill ${f.confidence === "low" ? "warn" : "gold"}" title="Lower-confidence detail">${f.confidence === "low" ? "Low" : "Medium"} confidence: confirm with your ${esc(who)}</span>`;
  };
  const fns = S.functions.map((f) => {
    const p = dparts(f.date);
    return `<div class="fn"><div class="date-chip"><div class="m">${p.m}</div><div class="d">${p.d}</div><div class="y">${p.y}</div></div>
      <div><h3>${esc(f.name)}</h3>
      <div class="meta">${p.wd} ${f.time_of_day} · ${esc(f.city)} · hosted by ${esc(hostLabel(f))} · ~${f.est_guests} guests ${S.dual ? `<span class="pill origin-${f.origin}">${f.origin === "shared" ? "Shared" : esc(S.couple.side_labels[f.origin])}</span>` : ""} ${confTag(f)}</div>
      <div class="rituals">${f.rituals.map(esc).join(" · ")}</div>
      ${f.merge_note && S.dual ? `<div class="note">${esc(f.merge_note)}</div>` : ""}</div></div>`;
  }).join("");
  let lastMonth = "";
  const tasks = S.tasks.map((t) => {
    const dp = dparts(t.due), month = `${dp.m} ${dp.y}`;
    const head = month !== lastMonth ? `<div class="month">${month}</div>` : "";
    lastMonth = month;
    const over = !t.done && daysUntil(t.due) < 0;
    return `${head}<label class="task ${t.done ? "done" : ""}"><input type="checkbox" data-task="${t.id}" ${t.done ? "checked" : ""}><span class="t">${esc(t.title)}${t.function_id ? ` <span class="pill">${esc(fnName(t.function_id))}</span>` : ""}</span><span class="due ${over ? "over" : ""}">${dp.d} ${dp.m}</span></label>`;
  }).join("");
  const budget = S.budget.map((b) => `<div class="bud"><span>${esc(b.category)}</span><div class="track"><div class="fill" style="width:${b.pct * 100 / 40 > 100 ? 100 : b.pct * 100 / 40}%"></div></div><span class="amt">${money(b.amount)} <span class="muted small">${b.pct}%</span></span></div>`).join("");
  const know = S.traditions_info.map((t) => `<div class="know"><h4>${esc(t.label)} · confirm with: ${esc(t.confirm_with.join(", "))}</h4><ul>${["dietary", "dress", "timing", "who_pays"].flatMap((k) => t.constraints[k].map((c) => `<li><b>${{ dietary: "Food", dress: "Dress", timing: "Timing", who_pays: "Who pays" }[k]}:</b> ${esc(c.rule)} ${c.confidence !== "high" ? `<span class="conf-${c.confidence}">${c.confidence} confidence</span>` : ""}</li>`)).join("")}</ul></div>`).join("");
  return `
    <div class="card"><h2>Functions</h2>${fns}</div>
    <div class="card"><h2>Checklist</h2>${tasks}</div>
    <div class="card"><h2>Budget split · ${money(S.budget_total)}</h2>${budget}<p class="small muted">Say "make photography 10%" or "set the budget to 30 lakh" in the chat; the other categories rebalance.</p></div>
    <div class="card"><h2>Good to know</h2><p class="small muted">General customs vary by family. Always confirm with your religious authority.</p>${know}</div>`;
}

function guestsView() {
  const fns = S.functions;
  const stat = (g, f) => { const v = g.rsvps[f.id]; return v ? `<button class="rs ${v}" data-g="${g.id}" data-f="${f.id}" data-v="${v}">${v}</button>` : '<span class="none">–</span>'; };
  const rows = S.guests.map((g) => `<tr><td><b>${esc(g.name)}</b></td><td>${esc(S.couple.side_labels[g.side])}</td><td>${g.party_size}</td>${fns.map((f) => `<td>${stat(g, f)}</td>`).join("")}</tr>`).join("");
  const foot = rsvpTotals().map(({ c }) => `<td>${c.yes} yes</td>`).join("");
  return `<div class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Guests and RSVPs</h2><span class="muted small">Click a status to change it. Or use the chat.</span></div>
    ${S.guests.length ? `<div class="table-wrap"><table class="guests"><thead><tr><th>Guest</th><th>Side</th><th>People</th>${fns.map((f) => `<th>${esc(f.name)}<br><span style="text-transform:none;font-weight:400">${dparts(f.date).d} ${dparts(f.date).m}</span></th>`).join("")}</tr></thead><tbody>${rows}</tbody>
    <tfoot><tr><td colspan="3">Headcount confirmed</td>${foot}</tr></tfoot></table></div>` : '<p class="muted" style="margin-top:12px">No guests yet. Try: "Add the Sharma family, 4 people, groom side, for the Sangeet".</p>'}</div>`;
}

function twoView() {
  const A = S.couple.side_labels.a, B = S.couple.side_labels.b;
  const la = S.traditions.find((t) => t.side === "a").label, lb = S.traditions.find((t) => t.side === "b").label;
  const byDate = {};
  S.functions.forEach((f) => (byDate[f.date] = byDate[f.date] || []).push(f));
  const card = (f) => `<div class="tl-card origin-${f.origin}"><b>${esc(f.name)}</b><span>${f.time_of_day} · ${esc(f.city)}</span>${f.merge_note ? `<br><span>${esc(f.merge_note)}</span>` : ""}</div>`;
  const lanes = Object.keys(byDate).sort().map((d) => {
    const items = byDate[d], p = dparts(d);
    const col = (o) => items.filter((f) => f.origin === o).map(card).join("");
    return `<div class="date-row">${p.wd} ${p.full}</div><div class="lane-cell">${col("a")}</div><div class="lane-cell">${col("shared")}</div><div class="lane-cell">${col("b")}</div>`;
  }).join("");
  const labelType = { dietary: "Food", dress: "Dress", timing: "Timing", who_pays: "Who pays" };
  const clashes = S.clashes.map((c) => `<div class="clash"><span class="pill gold">${labelType[c.type]}</span> ${c.functions.length ? `<b>${c.functions.map(esc).join(", ")}</b>` : ""}
    <div>${esc(c.description)}</div><div><b>Suggestion:</b> ${esc(c.suggestion)}</div><div class="why">${esc(c.rationale)}</div></div>`).join("");
  return `<div class="card"><h2>Two families, one timeline</h2><p class="muted small">${S.generation.merge && S.generation.merge.mode === "ai" ? "Merged by the Tradition-Merge agent." : "Merged by rules (AI merge was not used)."}</p>
    <div class="lanes"><div class="lane-h origin-a">${esc(A)}<br><span class="small">${esc(la)}</span></div><div class="lane-h origin-shared">Shared</div><div class="lane-h origin-b">${esc(B)}<br><span class="small">${esc(lb)}</span></div>${lanes}</div></div>
    <div class="card"><h2>Where the traditions clash</h2>${clashes || '<p class="muted">No clashes found.</p>'}</div>`;
}

function wireContent() {
  document.querySelectorAll("[data-done]").forEach((b) => (b.onclick = () => taskDone(b.dataset.done, true)));
  document.querySelectorAll("[data-task]").forEach((b) => (b.onchange = () => taskDone(b.dataset.task, b.checked)));
  const order = { pending: "yes", yes: "maybe", maybe: "no", no: "pending" };
  document.querySelectorAll(".rs").forEach((b) => (b.onclick = async () => {
    try { S = await api("/api/rsvp", { guest_id: b.dataset.g, function_id: b.dataset.f, status: order[b.dataset.v] }); render(false); } catch (e) { alert(e.message); }
  }));
}

async function taskDone(id, done) {
  try { S = await api("/api/task", { task_id: id, done }); render(false); } catch (e) { alert(e.message); }
}

/* ---------- chat ---------- */
const SUGGESTIONS = ["Add the Sharma family, 4 people, groom side, for the Sangeet", "What's my next step?", "Draft an invite for the Singh family for the Sangeet"];

function renderChat() {
  const body = $("#chat-body");
  if (!body) return;
  if (!chatMsgs.length) chatMsgs = [{ role: "bot", text: "Hi! I can add guests, update RSVPs, tweak the budget, tick off tasks and draft invites (nothing is sent). What would you like to do?" }];
  body.innerHTML = chatMsgs.map((m) => {
    if (m.role === "typing") return `<div class="typing">Thinking...</div>`;
    const acts = (m.actions || []).map((a) => {
      const r = a.result || {};
      const cls = r.error ? "err" : r.status === "pending_confirmation" ? "wait" : "";
      return `<span class="act ${cls}">${r.error ? "blocked" : r.status === "pending_confirmation" ? "awaiting you" : "done"}: ${esc(a.tool)}</span>`;
    }).join("");
    const drafts = (m.actions || []).filter((a) => a.tool === "draft_invite_message" && a.result && a.result.ok).map((a) =>
      `<div class="draft"><b>Draft for ${esc(a.result.guest)} · ${esc(a.result.function)}</b><p>${esc(a.result.text)}</p>
       <a class="btn small gold" href="${esc(a.result.wa_link)}" target="_blank" rel="noopener">Open in WhatsApp</a>
       <span class="small muted">Nothing is sent from this app.</span></div>`).join("");
    const errs = (m.actions || []).filter((a) => a.result && a.result.error).map((a) => `<div class="small muted" style="align-self:flex-start">${esc(a.result.error)}</div>`).join("");
    return `<div class="msg ${m.role === "user" ? "user" : "bot"}">${esc(m.text)}</div>${acts ? `<div class="acts">${acts}</div>` : ""}${errs}${drafts}`;
  }).join("");
  body.scrollTop = body.scrollHeight;
  const slot = $("#confirm-slot");
  slot.innerHTML = S.pending ? `<div class="confirm"><b>Please confirm</b><div>${esc(S.pending.label)}</div><div class="row"><button class="btn small" id="c-yes">Confirm</button><button class="btn small light" id="c-no">Cancel</button></div></div>` : "";
  if (S.pending) {
    $("#c-yes").onclick = () => confirmPending(true);
    $("#c-no").onclick = () => confirmPending(false);
  }
  $("#suggest").innerHTML = chatMsgs.length <= 2 ? SUGGESTIONS.map((s) => `<button type="button">${esc(s)}</button>`).join("") : "";
  document.querySelectorAll("#suggest button").forEach((b) => (b.onclick = () => sendChat(b.textContent)));
}

async function sendChat(text) {
  if (busy) return;
  busy = true;
  chatMsgs.push({ role: "user", text });
  chatMsgs.push({ role: "typing" });
  renderChat();
  try {
    const res = await api("/api/chat", { message: text });
    chatMsgs = chatMsgs.filter((m) => m.role !== "typing");
    chatMsgs.push({ role: "bot", text: res.reply, actions: res.actions });
    S = res.state;
  } catch (e) {
    chatMsgs = chatMsgs.filter((m) => m.role !== "typing");
    chatMsgs.push({ role: "bot", text: "Something went wrong: " + e.message });
  }
  busy = false;
  render(false);
}

async function confirmPending(approve) {
  try {
    const res = await api("/api/confirm", { approve });
    chatMsgs.push({ role: "bot", text: res.result || res.error });
    S = res.state;
  } catch (e) { chatMsgs.push({ role: "bot", text: e.message }); }
  render(false);
}

/* ---------- main render ---------- */
function render(full = true) {
  renderHeader();
  if (S.status !== "ready") { renderIntake(); return; }
  if (full || !$("#chat")) renderShell();
  else document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  renderContent();
  renderChat();
}

/* ---------- boot ---------- */
(async function init() {
  [META, S] = await Promise.all([api("/api/meta"), api("/api/state")]);
  if (S.chat && S.chat.length) chatMsgs = S.chat.map((m) => ({ role: m.role === "user" ? "user" : "bot", text: m.content }));
  render();
  $("#meter-btn").onclick = () => { $("#safety-pop").classList.add("hidden"); $("#meter-pop").classList.toggle("hidden"); };
  $("#safety-btn").onclick = () => { $("#meter-pop").classList.add("hidden"); $("#safety-pop").classList.toggle("hidden"); };
  $("#reset-btn").onclick = async () => { if (confirm("Start over? This clears the plan and guest list (token totals are kept).")) { S = await api("/api/reset", {}); chatMsgs = []; render(); } };
  document.addEventListener("click", (e) => { if (!e.target.closest(".popover, .chip-btn")) { $("#meter-pop").classList.add("hidden"); $("#safety-pop").classList.add("hidden"); } });
})();

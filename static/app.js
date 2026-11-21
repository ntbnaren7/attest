"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (p) => { const n = Math.abs(p) / 100; return (p < 0 ? "-" : "") + "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: p % 100 ? 2 : 0, maximumFractionDigits: 2 }); };
const ago = (iso) => { if (!iso) return "never"; const s = (Date.now() - new Date(iso)) / 1000; return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(iso).toLocaleString(); };

const state = { me: null, selected: null, conv: null, lastRanks: {}, pollTimer: null };

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers: { ...(opts.body && !(opts.body instanceof FormData) ? { "content-type": "application/json" } : {}), ...(opts.headers || {}) } });
  const text = await r.text();
  let body; try { body = JSON.parse(text); } catch { body = { detail: text }; }
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("login required"); }
  if (!r.ok) { const e = new Error(body.detail || body.error || r.statusText); e.body = body; e.status = r.status; throw e; }
  return body;
}

function showLogin() { $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); clearInterval(state.pollTimer); }

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try { await api("/api/login", { method: "POST", body: JSON.stringify(Object.fromEntries(f)) }); boot(); }
  catch (err) { $("#loginErr").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }); showLogin(); });

async function boot() {
  try { state.me = await api("/api/me"); } catch { return showLogin(); }
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  const op = state.me.operator;
  $("#tenant").textContent = op.merchant_name;
  $("#modeBadge").textContent = op.mode === "borrower" ? "Problem 2 · borrower book" : "B2B supplier adaptation";
  $("#who").textContent = `${op.display_name}${op.can_approve ? "" : " · cannot approve"}`;
  const ig = state.me.integrations;
  $("#integrations").innerHTML = ["cashfree", "sarvam", "relay"].map((k) => {
    const live = (k === "cashfree" && ig[k].mode === "cashfree_sandbox") || (k === "sarvam" && ig[k].mode === "live") || (k === "relay" && ig[k].mode === "inbound_webhook");
    return `<span class="badge ${live ? "b-live" : "b-fixture"}" title="${esc(ig[k].label)}">${k}: ${esc(ig[k].label)}</span>`;
  }).join("");
  await loadBatches();
  await refreshQueue();
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => { await refreshQueue(true); if (state.selected) loadCase(state.selected, true); }, 4000);
}

async function loadBatches() {
  const bs = await api("/api/batches");
  const sel = $("#batchSel"); const cur = sel.value;
  sel.innerHTML = `<option value="">All batches</option>` + bs.map((b) => `<option value="${esc(b.id)}">${esc(b.filename)} · ${b.valid_rows}/${b.total_rows} rows · ${new Date(b.imported_at).toLocaleTimeString()}</option>`).join("");
  sel.value = cur;
}
$("#batchSel").addEventListener("change", () => refreshQueue());

async function refreshQueue(quiet) {
  const q = await api("/api/queue" + ($("#batchSel").value ? `?batch_id=${encodeURIComponent($("#batchSel").value)}` : ""));
  $("#counts").textContent = `${q.counts.CONTACT} contact · ${q.counts.HOLD} hold · ${q.counts.REVIEW} review · ${q.counts.CLOSED} closed`;
  if (q.changes.length) {
    $("#changes").innerHTML = q.changes.map((c) => `<div class="change"><b>${esc(c.external_ref)}</b> ${esc(c.from)} → <b>${esc(c.to)}</b> (${esc(c.from_amount)} → ${esc(c.to_amount)}). ${esc(c.why)}</div>`).join("") + $("#changes").innerHTML;
  }
  const changed = new Set(q.changes.map((c) => c.account_id));
  $("#queueBody").innerHTML = q.rows.map((r) => `
    <tr data-id="${esc(r.account_id)}" class="${r.account_id === state.selected ? "sel" : ""} ${changed.has(r.account_id) ? "flash" : ""}">
      <td>${r.rank}</td>
      <td><b>${esc(r.external_ref)}</b><div class="muted small">${esc(r.display_name || "")} · ${esc(r.language || "")}</div></td>
      <td class="amt">${esc(r.outstanding_inr)}${r.disputed_paise ? `<div class="muted small">${inr(r.disputed_paise)} disputed</div>` : ""}</td>
      <td><span class="badge b-${r.decision}">${r.decision}</span>${r.decision === "CONTACT" && !r.contact_allowed_now ? `<div class="muted small">after 8 AM</div>` : ""}${r.decision === "CONTACT" ? `<div class="muted small">score ${r.score}</div>` : ""}</td>
      <td class="why">${esc(r.reason)}</td>
      <td class="small">${ago(r.last_verified_at)}${r.verification_stale ? ` <span class="badge b-warn">re-check before contact</span>` : ""}</td>
    </tr>`).join("") || `<tr><td colspan="6" class="muted">Import a batch to build the queue.</td></tr>`;
  for (const tr of $("#queueBody").querySelectorAll("tr[data-id]")) tr.addEventListener("click", () => { state.selected = tr.dataset.id; loadCase(tr.dataset.id); refreshQueue(true); });
}

$("#importForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  $("#importReport").innerHTML = `<p class="muted">Importing and checking payment evidence…</p>`;
  try {
    const r = await api("/api/import", { method: "POST", body: fd });
    $("#importReport").innerHTML = importReportHtml(r);
    await loadBatches(); $("#batchSel").value = r.batch_id; await refreshQueue();
  } catch (err) {
    const b = err.body || {};
    $("#importReport").innerHTML = `<p class="err">${esc(b.error || err.message)}</p>${b.accepted_names ? `<p class="small muted">Accepted column names: ${esc(JSON.stringify(b.accepted_names))}</p>` : ""}`;
  }
});

function importReportHtml(r) {
  return `<div class="report">
    <p><b>${r.valid_rows}</b> of ${r.total_rows} rows imported · <b class="${r.invalid_rows ? "err" : ""}">${r.invalid_rows} invalid</b> · ${r.claims_from_notes_and_refs} claims from notes/refs (to be verified)</p>
    ${r.protected_columns_dropped.length ? `<p>Dropped, never stored or scored: <code>${r.protected_columns_dropped.map(esc).join("</code> <code>")}</code></p>` : ""}
    ${r.held_out_columns.length ? `<p>Held out (outcome columns): <code>${r.held_out_columns.map(esc).join("</code> <code>")}</code></p>` : ""}
    ${r.amount_changes.length ? `<p>Amounts changed since last batch: ${r.amount_changes.map((c) => `${esc(c.invoice)} ${esc(c.from)} → ${esc(c.to)}`).join("; ")}</p>` : ""}
    ${r.invalid.length ? `<table>${r.invalid.map((i) => `<tr><td>row ${i.row}</td><td>${esc(i.account_ref || "—")}</td><td class="err">${i.errors.map(esc).join("; ")}</td></tr>`).join("")}</table>` : ""}
    ${r.unrecognized_columns.length ? `<p class="muted small">Ignored columns: ${r.unrecognized_columns.map(esc).join(", ")}</p>` : ""}
  </div>`;
}

const prov = (p) => `<span class="badge ${p === "fixture" ? "b-fixture" : "b-live"}">${p === "fixture" ? "FIXTURE" : esc(p)}</span>`;

async function loadCase(id, quiet) {
  let c;
  try { c = await api(`/api/cases/${encodeURIComponent(id)}`); } catch (e) { if (!quiet) $("#casePanel").innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const d = c.decision, a = c.account;
  const fixture = state.me.integrations.cashfree.mode === "fixture";
  const pending = c.approvals.find((x) => x.status === "pending");
  $("#casePanel").innerHTML = `
    <div class="row between"><h2>${esc(a.external_ref)} <span class="badge b-${d.decision}">${d.decision}</span></h2>
      <div class="row"><button class="ghost" id="btnRefresh">Re-check provider</button>
      ${d.next_actions.includes("request_payment_link") ? `<button id="btnLink">Request payment link (${inr(d.collectible_paise)})</button>` : ""}</div></div>
    <p>${esc(d.reason)}</p>
    ${d.route_to ? `<p class="small">Routed to: <b>${esc(d.route_to)}</b></p>` : ""}
    ${d.offered_options.length ? `<ul class="small">${d.offered_options.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>` : ""}
    ${d.factors.length ? `<p class="small muted">Priority factors (allow-listed only): ${d.factors.map(esc).join("; ")}</p>` : ""}
    <dl class="kv">
      <dt>Ledger due</dt><dd>${esc(c.totals.gross_due)}</dd>
      <dt>Payments received (verified)</dt><dd>${esc(c.totals.received)}</dd>
      <dt>Applied to invoices</dt><dd>${esc(c.totals.allocated)}</dd>
      <dt>Approved credits</dt><dd>${esc(c.totals.credits)}</dd>
      <dt>Still due</dt><dd><b>${esc(c.totals.outstanding)}</b></dd>
      <dt>Record version</dt><dd>v${a.version} · last provider check ${ago(a.provider_checked_at)}</dd>
      <dt>Language (messages only)</dt><dd>${esc(a.language)}</dd><dt>Recipient</dt><dd>${esc(a.phone || "—")}</dd>
    </dl>
    ${pending ? `<p><button id="btnApprove">Review approval: ${inr(pending.amount_paise)} link</button> <span class="muted small">requested by ${esc(pending.requested_by)} via ${esc(pending.requested_via)}</span></p>` : ""}
    <h3>Invoices / instalments</h3><ul class="list">${c.dues.map((x) => `<li>${esc(x.external_ref)} · ${inr(x.amount_paise)} · due ${esc(x.due_date || "—")}${x.payment_ref ? ` · ref <code>${esc(x.payment_ref)}</code>` : ""}</li>`).join("")}</ul>
    <h3>Payments received</h3><ul class="list">${c.receipts.map((x) => `<li>${prov(x.provenance)} <code>${esc(x.provider_payment_id)}</code> ${inr(x.amount_paise)} ${esc(x.payment_status)} · order ${esc(x.order_id || "")}</li>`).join("") || `<li class="muted">none verified</li>`}</ul>
    <h3>Allocation</h3><ul class="list">${c.allocations.map((x) => `<li>${inr(x.amount_paise)} → ${esc((c.dues.find((dd) => dd.id === x.due_id) || {}).external_ref)} · ${esc(x.basis)} · ledger: ${esc(x.ledger_status)}</li>`).join("") || `<li class="muted">nothing allocated</li>`}</ul>
    <h3>Claims (not facts until verified)</h3><ul class="list">${c.claims.map((x) => `<li><b>${esc(x.kind)}</b> ${x.amount_paise ? inr(x.amount_paise) : ""} ${x.reference ? `ref <code>${esc(x.reference)}</code>` : ""} ${x.promised_date ? `by ${esc(x.promised_date)}` : ""} · <i>${esc(x.status)}</i>${x.status_reason ? ` (${esc(x.status_reason)})` : ""} <span class="muted small">from ${esc(x.source)}</span></li>`).join("") || `<li class="muted">none</li>`}</ul>
    <h3>Payment links</h3><ul class="list">${c.links.map((x) => `<li>${prov(x.provenance)} <code>${esc(x.link_id)}</code> ${esc(x.status)} · ${inr(x.amount_paid_paise)} of ${inr(x.amount_paise)}
        ${x.provenance === "fixture" ? `<button class="ghost sim" data-link="${esc(x.link_id)}" data-half="1">Simulate part payment</button><button class="ghost sim" data-link="${esc(x.link_id)}">Simulate full payment</button>` : x.link_url ? ` · <a href="${esc(x.link_url)}" target="_blank" rel="noopener">open sandbox checkout</a>` : ""}</li>`).join("") || `<li class="muted">none</li>`}</ul>
    <h3>Outreach record (one delivery path per action)</h3><ul class="list">${c.outbox.map((o) => `<li><b>${esc(o.action)}</b> via ${esc(o.channel)} · ${esc(o.status)}${o.action === "deliver_link" && o.channel === "relay_manual" ? `<div class="small">Manual Relay step: open Relay → agent → Test flow, paste packet, Execute.</div><div class="packet">${esc(o.request_json)}</div>` : ""}</li>`).join("") || `<li class="muted">nothing sent</li>`}</ul>
    <h3>What the agent checked, delegated and did</h3>
    <ul class="list trace">${c.trace.slice(0, 40).map(traceLine).join("")}</ul>`;
  $("#btnRefresh")?.addEventListener("click", async () => { await api(`/api/cases/${id}/refresh`, { method: "POST" }); loadCase(id); refreshQueue(); });
  $("#btnLink")?.addEventListener("click", async () => { const r = await api(`/api/cases/${id}/request-link`, { method: "POST" }); if (r.status === "needs_input") openApproval(r.facts.approval, a); else alert(r.summary); loadCase(id); });
  $("#btnApprove")?.addEventListener("click", () => openApproval(pending, a));
  for (const b of $("#casePanel").querySelectorAll("button.sim")) b.addEventListener("click", async () => {
    const link = c.links.find((l) => l.link_id === b.dataset.link);
    const remaining = link.amount_paise - link.amount_paid_paise;
    const body = { link_id: link.link_id };
    if (b.dataset.half) body.amount_inr = String(Math.floor(remaining / 2 / 100));
    await api("/api/sim/pay", { method: "POST", body: JSON.stringify(body) });
    await refreshQueue(); loadCase(id);
  });
}

function traceLine(t) {
  const r = t.result || {};
  const what = t.kind === "handoff" ? `→ <b>${esc(t.target)}</b>.${esc(t.allowed_action)}: ${esc(r.status)} — ${esc(r.summary)}${r.source_ids && r.source_ids.length ? ` <span class="muted">[${r.source_ids.map(esc).join(", ")}]</span>` : ""}`
    : t.kind === "denial" ? `<span class="err">DENIED ${esc(t.allowed_action || "")}: ${esc(r.reason || "")}</span>`
    : t.kind === "queue_change" ? `queue: ${esc(r.from)} → <b>${esc(r.to)}</b>. ${esc(r.why)}`
    : `${esc(t.kind)} ${esc(t.target || "")} ${esc(t.allowed_action || "")} ${r.link_id ? esc(r.link_id) : ""} ${r.reason ? esc(r.reason) : ""}`;
  return `<li><span class="muted">${new Date(t.ts).toLocaleTimeString()}</span> <span class="who">${esc(t.actor)}</span> ${what}</li>`;
}

function openApproval(appr, acct) {
  const dlg = $("#approveDlg");
  $("#approveFields").innerHTML = `
    <dt>Account</dt><dd>${esc(acct.external_ref)} (${esc(appr.account_id)})</dd>
    <dt>Action</dt><dd>${esc(appr.action)}</dd>
    <dt>Amount</dt><dd><b>${inr(appr.amount_paise)}</b></dd>
    <dt>Recipient</dt><dd>${esc(appr.recipient)} (controlled test recipient)</dd>
    <dt>Record version</dt><dd>v${appr.account_version}</dd>
    <dt>Requested</dt><dd>${esc(appr.requested_by)} via ${esc(appr.requested_via)}</dd>`;
  $("#approveCheck").checked = false; $("#approveGo").disabled = true; $("#approveErr").textContent = "";
  $("#approveCheck").onchange = () => { $("#approveGo").disabled = !$("#approveCheck").checked; };
  $("#approveGo").onclick = async (e) => {
    e.preventDefault();
    const confirm = { account_id: appr.account_id, action: appr.action, amount_paise: appr.amount_paise, account_version: appr.account_version, recipient: appr.recipient };
    try {
      const h = { "x-approval-screen": "1" };
      const { token } = await api(`/api/approvals/${appr.id}/approve`, { method: "POST", headers: h, body: JSON.stringify({ confirm }) });
      await api(`/api/approvals/${appr.id}/execute`, { method: "POST", headers: h, body: JSON.stringify({ token }) });
      dlg.close(); await refreshQueue(); loadCase(appr.account_id);
    } catch (err) { $("#approveErr").textContent = `${err.body?.error || ""} ${err.message}`; }
  };
  dlg.showModal();
}

// ------------------------------------------------------------------ coordinator
function renderTurn(t) {
  const ex = t.extraction || {};
  const src = ex.source === "sarvam_tool_call" ? `<span class="badge b-live">Sarvam tool call</span>` : `<span class="badge b-fixture">rule-based extraction (no model)</span>`;
  const el = document.createElement("div"); el.className = "turn";
  el.innerHTML = `<div class="you">${t.via === "voice" ? "🎙 " : ""}${esc(t.transcript)}</div>
    <div class="small muted">${src} intent <b>${esc(ex.intent)}</b>${ex.amount_paise != null ? ` · amount ${inr(ex.amount_paise)}` : ""}${ex.reference ? ` · ref ${esc(ex.reference)}` : ""}${t.account ? ` · case ${esc(t.account.external_ref)}` : ""}${t.stt ? ` · STT ${esc(t.stt.model)} ${esc(t.stt.language_code || "")}` : ""}</div>
    ${t.handoffs?.length ? `<ol>${t.handoffs.map((h) => `<li>${esc(h.to)}.${esc(h.action)} → ${esc(h.status)}: ${esc(h.summary)}</li>`).join("")}</ol>` : ""}
    ${t.denials?.length ? `<p class="err small">${t.denials.map((d) => `${esc(d.code)}: ${esc(d.reason)}`).join("<br>")}</p>` : ""}
    <div class="reply">${esc(t.reply)}</div>
    ${t.model_error ? `<p class="err small">Model: ${esc(JSON.stringify(t.model_error))}</p>` : ""}`;
  if (t.reply_audio_b64) { const au = document.createElement("audio"); au.controls = true; au.src = "data:audio/wav;base64," + t.reply_audio_b64; el.appendChild(au); au.play().catch(() => {}); }
  $("#conv").prepend(el);
  state.conv = t.conversation_id;
  if (t.account) { state.selected = t.account.id; loadCase(t.account.id); }
  if (t.approval_request && t.account) openApproval(t.approval_request, { external_ref: t.account.external_ref });
  refreshQueue();
}

$("#textForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#textIn").value.trim(); if (!text) return;
  $("#textIn").value = "";
  try { renderTurn(await api("/api/coordinator/text", { method: "POST", body: JSON.stringify({ text, language: $("#lang").value || null, conversation_id: state.conv, account_id: state.selected }) })); }
  catch (err) { alert(err.message); }
});

let rec, chunks = [];
async function startRec() {
  if (!navigator.mediaDevices) return alert("Microphone not available in this browser");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  rec = new MediaRecorder(stream); chunks = [];
  rec.ondataavailable = (e) => chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
    const fd = new FormData();
    fd.append("audio", blob, "speech." + ((rec.mimeType || "").includes("mp4") ? "m4a" : "webm"));
    fd.append("language", $("#lang").value || "unknown");
    if (state.conv) fd.append("conversation_id", state.conv);
    if (state.selected) fd.append("account_id", state.selected);
    $("#micBtn").textContent = "… transcribing";
    try { renderTurn(await api("/api/coordinator/voice", { method: "POST", body: fd })); }
    catch (err) { alert(err.message); }
    finally { $("#micBtn").textContent = "● Hold to speak"; }
  };
  rec.start(); $("#micBtn").classList.add("rec"); $("#micBtn").textContent = "■ Release to send";
}
function stopRec() { if (rec && rec.state === "recording") { rec.stop(); $("#micBtn").classList.remove("rec"); } }
$("#micBtn").addEventListener("pointerdown", startRec);
$("#micBtn").addEventListener("pointerup", stopRec);
$("#micBtn").addEventListener("pointerleave", stopRec);

boot();

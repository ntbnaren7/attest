"""HTTP API + operator console."""
from __future__ import annotations

import html
import json
import logging
import os
import time

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from . import approvals, auth, coordinator, importer, live, recon, webhooks
from .config import ROOT, settings as default_settings
from .context import Ctx
from .db import now as dbnow
from .money import AmountError, format_inr, parse_rupees_to_paise
from .policy import load_facts
from .providers.cashfree import CashfreeFixture, ProviderError, sign_webhook
from .providers.sarvam import SarvamError
from .help_texts import HELP, LANGS as HELP_LANGS
from .queue import Reconciler, build_queue, reconcile_once
from .security import Security, SecurityMiddleware, client_ip, is_https
from .specialists import HandoffDenied, evaluate, handoff

log = logging.getLogger("receivables")
COOKIE = "rcv_session"
MAX_UTTERANCE_CHARS = 500   # one spoken or typed turn; keeps model cost per request bounded

TENANTS = [
    ("lender_demo", "Demo Finance - synthetic borrower book", "borrower",
     [("meena", "Meena (collections manager)", True), ("caller1", "Caller 1 (no approval rights)", False)]),
    ("supplier_demo", "Kovai Hardware Distributors - synthetic B2B supplier", "merchant",
     [("arun", "Arun (receivables operator)", True)]),
]


def seed(ctx: Ctx) -> None:
    pw = os.environ.get("DEMO_OPERATOR_PASSWORD", "demo-pass-123")
    for mid, name, mode, ops in TENANTS:
        if not ctx.db.one("SELECT 1 FROM merchants WHERE id=?", (mid,)):
            ctx.db.x("INSERT INTO merchants (id, name, mode) VALUES (?,?,?)", (mid, name, mode))
        for username, display, can_approve in ops:
            if not ctx.db.one("SELECT 1 FROM operators WHERE username=?", (username,)):
                auth.create_operator(ctx.db, mid, username, pw, display, can_approve)


def create_app(ctx: Ctx | None = None, start_reconciler: bool = True) -> FastAPI:
    ctx = ctx or Ctx(default_settings)
    seed(ctx)
    docs = os.environ.get("ENABLE_API_DOCS") == "1"   # off by default: no public schema of internal routes
    app = FastAPI(title="Receivables verification queue", docs_url="/docs" if docs else None,
                  redoc_url=None, openapi_url="/openapi.json" if docs else None)
    app.state.ctx = ctx
    sec = Security()
    app.state.security = sec
    allowed = {o.rstrip("/") for o in (ctx.settings.public_base_url, "http://localhost:8000", "http://127.0.0.1:8000",
                                       "http://localhost:5173", "http://127.0.0.1:5173") if o}
    app.add_middleware(SecurityMiddleware, security=sec, allowed_origins=allowed)
    if start_reconciler:
        @app.on_event("startup")
        def _start():
            app.state.reconciler = Reconciler(ctx)
            app.state.reconciler.start()

    def operator(request: Request) -> auth.Operator:
        op = auth.operator_for_token(ctx.db, request.cookies.get(COOKIE))
        if not op:
            raise HTTPException(401, "login required")
        return op

    def screen_channel(request: Request) -> str:
        # The approval screen sends this header; cross-site forms cannot set it, and the voice path never does.
        return approvals.APPROVAL_CHANNEL if request.headers.get("x-approval-screen") == "1" else "unknown"

    live.register(app, ctx, sec, operator)

    # ---------------------------------------------------------------- reconciliation (teammate engine, recon/)
    @app.get("/api/recon/status")
    def recon_status(op: auth.Operator = Depends(operator)):
        ok, why = recon.available()
        return {"available": ok, "reason": why, "mode": recon.mode(), "payouts_live": recon.payouts_live(),
                "run": recon.RUN, "agents": recon.AGENTS}

    @app.post("/api/recon/run")
    async def recon_run(op: auth.Operator = Depends(operator)):
        sec.limit("provider", op.id)
        ok, why = recon.available()
        if not ok:
            raise HTTPException(503, f"Reconciliation engine unavailable: {why}")
        return await run_in_threadpool(recon.run, op.merchant_id)

    @app.post("/api/recon/approve")
    async def recon_approve(request: Request, op: auth.Operator = Depends(operator)):
        sec.limit("money", op.id)
        body = await request.json()
        try:
            return await run_in_threadpool(recon.approve, op.merchant_id, str(body.get("thread_id", "")),
                                           dict(body.get("decisions") or {}))
        except KeyError:
            raise HTTPException(404, "Unknown reconciliation run.")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.exception_handler(approvals.ApprovalError)
    def _appr_err(_, e: approvals.ApprovalError):
        return JSONResponse({"error": e.code, "detail": str(e)}, status_code=409 if e.code in ("STALE_APPROVAL", "NOT_PENDING") else 403)

    @app.exception_handler(HandoffDenied)
    def _hd(_, e):
        return JSONResponse({"error": "DENIED", "detail": str(e)}, status_code=404)

    # ---------------------------------------------------------------- auth
    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        username = str(body.get("username", ""))[:64]
        sec.limit("login", client_ip(request))
        sec.logins.check(username)
        token = auth.login(ctx.db, username, str(body.get("password", ""))[:256])
        if not token:
            sec.logins.failed(username)
            raise HTTPException(401, "invalid credentials")
        sec.logins.succeeded(username)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=12 * 3600, secure=is_https(request))
        return resp

    @app.post("/api/logout")
    def logout(request: Request):
        if request.cookies.get(COOKIE):
            auth.logout(ctx.db, request.cookies[COOKIE])
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    # ---------------------------------------------------------------- demo scenarios (no login screen)
    DEMO_PERSONAS = {
        "meena": {"label": "Loan book", "detail": "Problem 2 · borrower book"},
        "arun": {"label": "Supplier invoices", "detail": "B2B supplier adaptation"},
    }

    def _demo_allowed(request: Request) -> bool:
        """Demo sign-in only on this machine: DEMO_MODE=1, a loopback peer, no proxy hop, and a localhost Host header."""
        if os.environ.get("DEMO_MODE") != "1":
            return False
        peer = request.client.host if request.client else ""
        host = (request.headers.get("host") or "").split(":")[0]
        forwarded = any(request.headers.get(h) for h in ("x-forwarded-for", "x-forwarded-host", "forwarded", "x-real-ip"))
        return peer in ("127.0.0.1", "::1", "testclient") and host in ("localhost", "127.0.0.1", "testserver") and not forwarded

    @app.get("/api/demo")
    def demo_info(request: Request):
        ok = _demo_allowed(request)
        return {"enabled": ok, "personas": DEMO_PERSONAS if ok else {}}

    @app.post("/api/demo/session")
    async def demo_session(request: Request):
        if not _demo_allowed(request):
            raise HTTPException(403, "Demo sign-in is only available on this machine with DEMO_MODE=1.")
        sec.limit("login", client_ip(request))
        persona = str((await request.json()).get("persona", "meena"))
        if persona not in DEMO_PERSONAS:
            raise HTTPException(400, "unknown scenario")
        token = auth.session_for(ctx.db, persona)
        if not token:
            raise HTTPException(404, "scenario user missing")
        resp = JSONResponse({"ok": True, "persona": persona})
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=12 * 3600)
        return resp

    @app.get("/api/me")
    def me(request: Request, op: auth.Operator = Depends(operator)):
        return {"operator": op.__dict__, "integrations": ctx.integration_status(op.merchant_id),
                "demo": _demo_allowed(request), "personas": DEMO_PERSONAS if _demo_allowed(request) else {},
                "test_recipients": list(ctx.settings.test_recipients)}

    # ---------------------------------------------------------------- import + queue
    @app.post("/api/import")
    async def do_import(file: UploadFile = File(...), op: auth.Operator = Depends(operator)):
        sec.limit("import", op.id)
        content = await file.read()
        if len(content) > 5_000_000:
            raise HTTPException(413, "file too large")
        rep = importer.import_batch(ctx, op.merchant_id, file.filename or "upload.csv", content, f"operator:{op.username}")
        if rep.get("ok"):
            rep["queue"] = build_queue(ctx, op.merchant_id, rep["batch_id"])["counts"]
        return JSONResponse(rep, status_code=200 if rep.get("ok") else 400)

    @app.get("/api/batches")
    def batches(op: auth.Operator = Depends(operator)):
        return ctx.db.all("SELECT id, filename, imported_at, total_rows, valid_rows, invalid_rows FROM batches "
                          "WHERE merchant_id=? ORDER BY imported_at DESC", (op.merchant_id,))

    @app.get("/api/queue")
    def queue(batch_id: str | None = None, lang: str | None = None, op: auth.Operator = Depends(operator)):
        q = build_queue(ctx, op.merchant_id, batch_id or None)
        for r in q["rows"]:
            r["reason_local"] = _reason_local(r, lang)
        return q

    def _reason_local(r: dict, lang: str | None) -> str:
        if lang not in ("hi-IN", "ta-IN", "kn-IN"):
            return r["reason"]
        from .policy import Decision
        d = Decision(**{k: r[k] for k in Decision.__dataclass_fields__})
        return coordinator.explain(lang, r["external_ref"], d)

    @app.get("/api/cases/{case_id}/explain")
    def explain_case(case_id: str, lang: str = "en-IN", op: auth.Operator = Depends(operator)):
        """Plain-language reason for one case, optionally spoken (Sarvam TTS) for people who prefer listening."""
        acct = ctx.db.one("SELECT external_ref FROM accounts WHERE id=? AND merchant_id=?", (case_id, op.merchant_id))
        if not acct:
            raise HTTPException(404, "not found")
        lang = lang if lang in coordinator.LANGS else "en-IN"
        d = evaluate(ctx, op.merchant_id, case_id)
        return _spoken(op, coordinator.explain(lang, acct["external_ref"], d), lang)

    def _spoken(op, text: str, lang: str) -> dict:
        """Text plus Sarvam TTS audio; rate limited, budgeted, and free when cached."""
        sec.limit("speech", op.id)
        out = {"text": text, "language": lang, "audio_b64": None}
        if ctx.sarvam:
            if not ctx.sarvam.tts_cached(text, lang):
                sec.budget.charge(op.merchant_id, 1)
            try:
                out["audio_b64"] = ctx.sarvam.tts(text, lang)["audio_b64"]
            except SarvamError as e:
                out["tts_error"] = str(e)[:200]
        return out

    @app.get("/api/help/{key}")
    def spoken_help(key: str, lang: str = "en-IN", op: auth.Operator = Depends(operator)):
        """Explain a dashboard action aloud. Only fixed help texts can be spoken; no free text reaches TTS."""
        entry = HELP.get(key)
        if not entry:
            raise HTTPException(404, "unknown help topic")
        lang = lang if lang in HELP_LANGS else "en-IN"
        return _spoken(op, entry[lang], lang)

    @app.get("/api/cases/{case_id}")
    def case(case_id: str, op: auth.Operator = Depends(operator)):
        f = load_facts(ctx.db, op.merchant_id, case_id)
        if not f:
            raise HTTPException(404, "not found")
        d = evaluate(ctx, op.merchant_id, case_id)
        acct = dict(f.account)
        acct["features"] = f.features
        acct.pop("features_json", None)
        return {"account": acct, "decision": d.as_dict(), "dues": f.dues, "credits": f.credits, "claims": f.claims,
                "receipts": f.receipts, "allocations": f.allocations, "links": f.links,
                "totals": {"gross_due": format_inr(f.gross_due), "received": format_inr(f.received),
                           "allocated": format_inr(f.allocated), "credits": format_inr(f.credit_total),
                           "outstanding": format_inr(f.outstanding)},
                "approvals": ctx.db.all("SELECT * FROM approvals WHERE account_id=? AND merchant_id=? ORDER BY created_at DESC",
                                        (case_id, op.merchant_id)),
                "outbox": ctx.db.all("SELECT * FROM outbox WHERE account_id=? AND merchant_id=? ORDER BY created_at DESC",
                                     (case_id, op.merchant_id)),
                "trace": _trace(op.merchant_id, account_id=case_id),
                # batch id -> file name, so each claim can show which list it came from
                "batch_files": {b["id"]: b["filename"] for b in ctx.db.all(
                    "SELECT id, filename FROM batches WHERE merchant_id=?", (op.merchant_id,))}}

    def _trace(merchant_id, account_id=None, conversation_id=None, limit=200):
        sql, p = "SELECT * FROM trace WHERE merchant_id=?", [merchant_id]
        if account_id:
            sql += " AND account_id=?"; p.append(account_id)
        if conversation_id:
            sql += " AND conversation_id=?"; p.append(conversation_id)
        rows = ctx.db.all(sql + " ORDER BY id DESC LIMIT ?", (*p, limit))
        for r in rows:
            for k in ("input_json", "result_json"):
                r[k[:-5]] = json.loads(r.pop(k)) if r.get(k) else None
        return rows

    @app.get("/api/trace")
    def trace(account_id: str | None = None, conversation_id: str | None = None, op: auth.Operator = Depends(operator)):
        return _trace(op.merchant_id, account_id, conversation_id)

    @app.post("/api/cases/{case_id}/refresh")
    def refresh(case_id: str, op: auth.Operator = Depends(operator)):
        sec.limit("provider", op.id)
        r = handoff(ctx, op.merchant_id, case_id, "evidence", "refresh_account", actor=f"operator:{op.username}")
        return {"refresh": r.as_dict(), "decision": evaluate(ctx, op.merchant_id, case_id).as_dict()}

    @app.post("/api/cases/{case_id}/request-link")
    def request_link(case_id: str, op: auth.Operator = Depends(operator)):
        sec.limit("money", op.id)
        r = handoff(ctx, op.merchant_id, case_id, "outreach", "propose_payment_link", actor=f"operator:{op.username}",
                    requested_by=f"operator:{op.username}", requested_via="ui")
        return r.as_dict()

    # ---------------------------------------------------------------- approvals
    @app.get("/api/approvals")
    def list_approvals(op: auth.Operator = Depends(operator)):
        return ctx.db.all("SELECT a.*, ac.external_ref FROM approvals a JOIN accounts ac ON ac.id=a.account_id "
                          "WHERE a.merchant_id=? ORDER BY a.created_at DESC LIMIT 50", (op.merchant_id,))

    @app.post("/api/approvals/{approval_id}/approve")
    async def approve(approval_id: str, request: Request, op: auth.Operator = Depends(operator)):
        sec.limit("money", op.id)
        body = await request.json()
        return approvals.approve(ctx, op, approval_id, body.get("confirm") or {}, screen_channel(request))

    @app.post("/api/approvals/{approval_id}/execute")
    async def execute(approval_id: str, request: Request, op: auth.Operator = Depends(operator)):
        sec.limit("money", op.id)
        body = await request.json()
        res = approvals.execute(ctx, op, approval_id, body.get("token", ""), screen_channel(request))
        build_queue(ctx, op.merchant_id)
        return res

    # ---------------------------------------------------------------- coordinator
    @app.post("/api/coordinator/text")
    async def coord_text(request: Request, op: auth.Operator = Depends(operator)):
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty")
        if len(text) > MAX_UTTERANCE_CHARS:
            raise HTTPException(413, f"Please keep it under {MAX_UTTERANCE_CHARS} characters.")
        sec.limit("assistant", op.id)
        if ctx.sarvam:
            sec.budget.charge(op.merchant_id, 2)          # chat tool call + spoken reply
        sec.inflight.acquire(op.id)
        try:
            return await run_in_threadpool(
                coordinator.process, ctx, op, text, language_hint=body.get("language"), via="text",
                conversation_id=body.get("conversation_id"), account_hint=body.get("account_id"))
        finally:
            sec.inflight.release(op.id)

    @app.post("/api/coordinator/voice")
    async def coord_voice(audio: UploadFile = File(...), language: str = Form("unknown"),
                          conversation_id: str = Form(""), account_id: str = Form(""),
                          op: auth.Operator = Depends(operator)):
        if not ctx.sarvam:
            raise HTTPException(503, "Sarvam is not configured (SARVAM_API_KEY missing): voice is blocked; use text.")
        sec.limit("assistant", op.id)
        data = await audio.read()
        if len(data) > 1_500_000:
            raise HTTPException(413, "Audio too long. Please keep it under 30 seconds.")
        if len(data) < 1_000:
            raise HTTPException(400, "No speech recorded.")
        sec.budget.charge(op.merchant_id, 3)              # speech-to-text + chat tool call + spoken reply
        sec.inflight.acquire(op.id)
        try:
            t0 = time.time()
            try:
                stt = await run_in_threadpool(ctx.sarvam.stt, data, audio.filename or "speech.webm",
                                              audio.content_type or "audio/webm",
                                              language if language in coordinator.LANGS else "unknown")
            except SarvamError as e:
                raise HTTPException(502, f"speech-to-text failed: {e}")
            stt["latency_ms"] = int((time.time() - t0) * 1000)
            if not stt["transcript"].strip():
                return {"transcript": "", "reply": "I did not catch that. Please repeat.", "stt": stt}
            transcript = stt["transcript"][:MAX_UTTERANCE_CHARS]
            return await run_in_threadpool(
                coordinator.process, ctx, op, transcript, language_hint=stt.get("language_code"), via="voice",
                conversation_id=conversation_id or None, stt=stt, account_hint=account_id or None)
        finally:
            sec.inflight.release(op.id)

    # ---------------------------------------------------------------- provider callbacks
    @app.post("/webhooks/cashfree")
    async def cf_webhook(request: Request):
        raw = await request.body()
        status, body = webhooks.process(ctx, raw, dict(request.headers))
        return JSONResponse(body, status_code=status)

    # ---------------------------------------------------------------- fixture simulation (labeled)
    def _fixture(op) -> CashfreeFixture:
        cf = ctx.cashfree_for(op.merchant_id)
        if not isinstance(cf, CashfreeFixture):
            raise HTTPException(409, "This tenant uses the live Cashfree sandbox; pay through the real link instead.")
        return cf

    @app.post("/api/sim/pay")
    async def sim_pay(request: Request, op: auth.Operator = Depends(operator)):
        sec.limit("money", op.id)
        body = await request.json()
        cf = _fixture(op)
        link = ctx.db.one("SELECT * FROM payment_links WHERE link_id=? AND merchant_id=?", (body.get("link_id"), op.merchant_id))
        if not link:
            raise HTTPException(404, "link not found")
        try:
            amount = parse_rupees_to_paise(body.get("amount_inr")) if body.get("amount_inr") else \
                link["amount_paise"] - link["amount_paid_paise"]
            event = cf.simulate_payment(link["link_id"], amount, body.get("status", "SUCCESS"))
        except (AmountError, ProviderError) as e:
            raise HTTPException(400, str(e))
        out = {"simulated": True, "event": event}
        if body.get("deliver_webhook", True):
            raw = json.dumps(event, separators=(",", ":")).encode()
            ts = str(int(dbnow().timestamp() * 1000))
            hdrs = {"x-webhook-timestamp": ts, "x-webhook-signature": sign_webhook(raw, ts, cf.webhook_secret),
                    "x-idempotency-key": f"sim-{event['data']['order']['transaction_id']}"}
            out["webhook"] = webhooks.process(ctx, raw, hdrs)[1]
            if body.get("duplicate"):
                out["webhook_duplicate"] = webhooks.process(ctx, raw, hdrs)[1]
        return out

    @app.get("/sim/pay/{link_id}", response_class=HTMLResponse)
    def sim_page(link_id: str):
        return HTMLResponse(f"<!doctype html><title>Simulated checkout</title><body style='font-family:sans-serif;padding:2rem'>"
                            f"<h2>SIMULATED checkout (fixture mode)</h2><p>Link <code>{html.escape(link_id)}</code> is a fixture link, not a "
                            "Cashfree sandbox link. Use the 'Simulate payment' button in the operator console.</p></body>")

    @app.post("/api/reconcile")
    def reconcile(op: auth.Operator = Depends(operator)):
        sec.limit("provider", op.id)
        return {"checked": reconcile_once(ctx)}

    @app.get("/api/integrations")
    def integrations(op: auth.Operator = Depends(operator)):
        return ctx.integration_status(op.merchant_id)

    # ---------------------------------------------------------------- UI
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    dist = ROOT / "web" / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    @app.get("/favicon.svg")
    def favicon():
        f = dist / "favicon.svg"
        return FileResponse(str(f)) if f.exists() else Response(status_code=404)

    @app.get("/classic")
    def classic():
        return FileResponse(str(ROOT / "static" / "index.html"))

    @app.get("/")
    def index():
        # The dashboard build (web/dist) when present; the classic console otherwise.
        built = dist / "index.html"
        return FileResponse(str(built if built.exists() else ROOT / "static" / "index.html"))

    return app


app = create_app() if os.environ.get("RECEIVABLES_NO_AUTOAPP") != "1" else None

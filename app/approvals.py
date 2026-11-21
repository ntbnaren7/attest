"""Approval binding and execution of the one financial action the agent may take: creating a
Cashfree payment link for the verified remainder.

Rules enforced here, regardless of what any agent or model asks for:
  * Only an authenticated operator session on the approval screen ('ui_session') can approve or
    execute. Voice is never an approval channel.
  * An approval binds merchant, case, action, amount (paise), record version and recipient.
  * Execution re-reads provider state first; any change to the record version makes the approval stale.
  * One link per approval (outbox idempotency key + provider x-idempotency-key).
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import timedelta

from .auth import sha256
from .context import Ctx
from .db import iso, new_id, now, parse_iso
from .money import format_inr
from .policy import CONTACT
from .specialists import evaluate, handoff

APPROVAL_CHANNEL = "ui_session"


class ApprovalError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _deny(ctx: Ctx, merchant_id: str, account_id: str | None, code: str, message: str, actor: str, detail=None):
    ctx.db.trace(merchant_id, "denial", actor, account_id=account_id, target="approvals", allowed_action=code,
                 input=detail, result={"code": code, "reason": message})
    raise ApprovalError(code, message)


def _public(a: dict) -> dict:
    return {k: a[k] for k in ("id", "account_id", "action", "amount_paise", "account_version", "recipient", "status",
                              "status_reason", "requested_by", "requested_via", "approved_by", "created_at",
                              "approved_at", "expires_at", "used_at")} | {"amount_inr": format_inr(a["amount_paise"])}


def request(ctx: Ctx, merchant_id: str, account_id: str, action: str, requested_by: str, requested_via: str) -> dict:
    if action != "create_payment_link":
        _deny(ctx, merchant_id, account_id, "ACTION_NOT_PERMITTED", f"{action} is outside the agent's permissions", requested_by)
    acct = ctx.db.one("SELECT * FROM accounts WHERE id=? AND merchant_id=?", (account_id, merchant_id))
    if not acct:
        _deny(ctx, merchant_id, None, "NOT_FOUND", "case not in this merchant's book", requested_by)
    d = evaluate(ctx, merchant_id, account_id)
    if d.decision != CONTACT or d.collectible_paise <= 0:
        _deny(ctx, merchant_id, account_id, "NOT_ELIGIBLE", d.reason, requested_by)
    if (acct["phone"] or "") not in ctx.settings.test_recipients:
        _deny(ctx, merchant_id, account_id, "RECIPIENT_NOT_CONTROLLED",
              "Recipient is not on the controlled test-recipient list; outreach refused.", requested_by)
    existing = ctx.db.one("SELECT * FROM approvals WHERE merchant_id=? AND account_id=? AND action=? AND account_version=?"
                          " AND amount_paise=? AND status IN ('pending','approved')",
                          (merchant_id, account_id, action, acct["version"], d.collectible_paise))
    if existing:
        return _public(existing)
    aid = new_id("apr")
    ctx.db.x("INSERT INTO approvals (id, merchant_id, account_id, action, amount_paise, account_version, recipient,"
             " requested_by, requested_via, status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
             (aid, merchant_id, account_id, action, d.collectible_paise, acct["version"], acct["phone"], requested_by,
              requested_via, "pending", iso(), iso(now() + timedelta(seconds=ctx.settings.approval_ttl_seconds))))
    a = ctx.db.one("SELECT * FROM approvals WHERE id=?", (aid,))
    ctx.db.trace(merchant_id, "approval_requested", requested_by, account_id=account_id, target="approvals",
                 allowed_action=action, result=_public(a))
    return _public(a)


def _load(ctx: Ctx, merchant_id: str, approval_id: str) -> dict:
    a = ctx.db.one("SELECT * FROM approvals WHERE id=? AND merchant_id=?", (approval_id, merchant_id))
    if not a:
        _deny(ctx, merchant_id, None, "NOT_FOUND", "approval not found for this merchant", "approvals")
    return a


def _mark(ctx: Ctx, approval_id: str, status: str, reason: str):
    ctx.db.x("UPDATE approvals SET status=?, status_reason=? WHERE id=?", (status, reason, approval_id))


def _check_current(ctx: Ctx, a: dict, actor: str) -> None:
    acct = ctx.db.one("SELECT * FROM accounts WHERE id=?", (a["account_id"],))
    d = evaluate(ctx, a["merchant_id"], a["account_id"])
    problems = []
    if acct["version"] != a["account_version"]:
        problems.append(f"record changed since approval (version {a['account_version']} -> {acct['version']})")
    if d.decision != CONTACT:
        problems.append(f"case is now {d.decision}: {d.reason}")
    elif d.collectible_paise != a["amount_paise"]:
        problems.append(f"amount due is now {format_inr(d.collectible_paise)}, approval was for {format_inr(a['amount_paise'])}")
    if (acct["phone"] or "") != a["recipient"]:
        problems.append("recipient changed")
    if problems:
        _mark(ctx, a["id"], "stale", "; ".join(problems))
        _deny(ctx, a["merchant_id"], a["account_id"], "STALE_APPROVAL", "; ".join(problems), actor)


def approve(ctx: Ctx, operator, approval_id: str, confirm: dict, channel: str) -> dict:
    """Bind an operator's explicit on-screen confirmation to the exact request. Returns a one-time token."""
    actor = f"operator:{operator.username}"
    if channel != APPROVAL_CHANNEL:
        _deny(ctx, operator.merchant_id, None, "VOICE_CANNOT_AUTHORIZE" if channel == "voice" else "CHANNEL_NOT_PERMITTED",
              "Approval must be given by an authenticated operator on the approval screen; voice cannot authorize "
              "a financial action.", actor, {"approval_id": approval_id, "channel": channel})
    a = _load(ctx, operator.merchant_id, approval_id)
    if not operator.can_approve:
        _deny(ctx, a["merchant_id"], a["account_id"], "NO_APPROVAL_RIGHT", "operator cannot approve", actor)
    if a["status"] != "pending":
        _deny(ctx, a["merchant_id"], a["account_id"], "NOT_PENDING", f"approval is {a['status']}", actor)
    if parse_iso(a["expires_at"]) < now():
        _mark(ctx, a["id"], "expired", "approval window passed")
        _deny(ctx, a["merchant_id"], a["account_id"], "EXPIRED", "approval request expired", actor)
    for k in ("account_id", "action", "amount_paise", "account_version", "recipient"):
        if confirm.get(k) != a[k]:
            _deny(ctx, a["merchant_id"], a["account_id"], "BINDING_MISMATCH",
                  f"confirmed {k} does not match the request", actor, {"field": k})
    _check_current(ctx, a, actor)
    token = secrets.token_urlsafe(24)
    ctx.db.x("UPDATE approvals SET status='approved', approved_by=?, approved_via=?, token_hash=?, approved_at=?, expires_at=?"
             " WHERE id=? AND status='pending'",
             (operator.id, channel, sha256(token), iso(), iso(now() + timedelta(seconds=ctx.settings.approval_ttl_seconds)), a["id"]))
    ctx.db.trace(a["merchant_id"], "approval_granted", actor, account_id=a["account_id"], target="approvals",
                 allowed_action=a["action"], result={"approval_id": a["id"], "amount_paise": a["amount_paise"],
                                                     "account_version": a["account_version"], "recipient": a["recipient"]})
    return {"approval_id": a["id"], "token": token}


def execute(ctx: Ctx, operator, approval_id: str, token: str, channel: str) -> dict:
    actor = f"operator:{operator.username}"
    if channel != APPROVAL_CHANNEL:
        _deny(ctx, operator.merchant_id, None, "VOICE_CANNOT_AUTHORIZE" if channel == "voice" else "CHANNEL_NOT_PERMITTED",
              "Execution needs an authenticated on-screen approval; voice cannot authorize it.", actor)
    a = _load(ctx, operator.merchant_id, approval_id)
    if a["status"] == "used":
        link = ctx.db.one("SELECT * FROM payment_links WHERE approval_id=?", (a["id"],))
        return {"status": "already_executed", "link": link}
    if a["status"] != "approved" or not token or sha256(token) != a["token_hash"]:
        _deny(ctx, a["merchant_id"], a["account_id"], "INVALID_APPROVAL", "approval token missing, wrong or not approved", actor)
    if parse_iso(a["expires_at"]) < now():
        _mark(ctx, a["id"], "expired", "approval expired before execution")
        _deny(ctx, a["merchant_id"], a["account_id"], "EXPIRED", "approval expired", actor)

    # Refresh authoritative state before acting: a payment may have landed since approval.
    handoff(ctx, a["merchant_id"], a["account_id"], "evidence", "refresh_account", actor=actor)
    _check_current(ctx, a, actor)

    idem = f"link:{a['id']}"
    ob = ctx.db.one("SELECT * FROM outbox WHERE idem_key=?", (idem,))
    if ob and ob["status"] == "done":
        return {"status": "already_executed", "link": ctx.db.one("SELECT * FROM payment_links WHERE approval_id=?", (a["id"],))}
    if not ob:
        ctx.db.x("INSERT INTO outbox (id, merchant_id, account_id, action, idem_key, channel, status, request_json, created_at)"
                 " VALUES (?,?,?,?,?,?,?,?,?)", (new_id("ob"), a["merchant_id"], a["account_id"], "create_payment_link",
                                                  idem, "cashfree", "pending", json.dumps(_public(a)), iso()))
    acct = ctx.db.one("SELECT * FROM accounts WHERE id=?", (a["account_id"],))
    mode = ctx.db.one("SELECT mode FROM merchants WHERE id=?", (a["merchant_id"],))["mode"]
    cf = ctx.cashfree_for(a["merchant_id"])
    link_id = f"rcv_{a['id'][4:]}"
    provider_idem = str(uuid.uuid5(uuid.NAMESPACE_URL, f"receivables:{a['id']}"))
    min_partial = None
    if a["amount_paise"] >= 200000:
        min_partial = max(100, (a["amount_paise"] // 4) // 100 * 100)  # part payment allowed; 25% minimum first amount
    from .providers.cashfree import ProviderError
    try:
        state = cf.create_link(
            link_id=link_id, amount_paise=a["amount_paise"], customer_phone=acct["phone"],
            customer_name=acct["display_name"] or "Test payer", customer_email=acct["email"],
            purpose=(f"Loan instalment {acct['external_ref']}" if mode == "borrower" else f"Invoices {acct['external_ref']}"),
            expiry=now() + timedelta(days=7), notes={"case_id": a["account_id"], "approval_id": a["id"],
                                                      "account_ref": acct["external_ref"], "synthetic": "true"},
            notify_url=f"{ctx.settings.public_base_url}/webhooks/cashfree", return_url=None,
            idem_key=provider_idem, allow_partial=True, min_partial_paise=min_partial)
    except ProviderError as e:
        ctx.db.x("UPDATE outbox SET status='failed', response_json=? WHERE idem_key=?", (json.dumps({"error": str(e)}), idem))
        ctx.db.trace(a["merchant_id"], "action_failed", actor, account_id=a["account_id"], target="cashfree",
                     allowed_action="create_payment_link", result={"error": str(e), "kind": e.kind})
        raise ApprovalError("PROVIDER_ERROR", f"Cashfree did not create the link: {e}")
    if state.amount_paise != a["amount_paise"]:
        ctx.db.x("UPDATE accounts SET evidence_conflict=? WHERE id=?",
                 (f"provider link amount {format_inr(state.amount_paise)} differs from approved {format_inr(a['amount_paise'])}",
                  a["account_id"]))
    with ctx.db.tx():
        ctx.db.x("INSERT OR IGNORE INTO payment_links (link_id, merchant_id, account_id, approval_id, amount_paise, amount_paid_paise,"
                 " status, link_url, provenance, created_at, last_checked_at, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (state.link_id, a["merchant_id"], a["account_id"], a["id"], state.amount_paise, state.amount_paid_paise,
                  state.status, state.link_url, state.provenance, iso(), iso(), json.dumps(state.raw, default=str)[:4000]))
        ctx.db.x("UPDATE approvals SET status='used', used_at=? WHERE id=?", (iso(), a["id"]))
        ctx.db.x("UPDATE outbox SET status='done', response_json=? WHERE idem_key=?",
                 (json.dumps({"link_id": state.link_id, "link_url": state.link_url, "status": state.status,
                              "provenance": state.provenance}), idem))
        ctx.db.bump_version(a["account_id"])
    ctx.db.trace(a["merchant_id"], "action_executed", actor, account_id=a["account_id"], target="cashfree",
                 allowed_action="create_payment_link",
                 result={"link_id": state.link_id, "amount_paise": state.amount_paise, "status": state.status,
                         "provenance": state.provenance})
    delivery = handoff(ctx, a["merchant_id"], a["account_id"], "outreach", "deliver_link", actor=actor, link_id=state.link_id)
    return {"status": "executed", "link": ctx.db.one("SELECT * FROM payment_links WHERE link_id=?", (state.link_id,)),
            "delivery": delivery.as_dict()}

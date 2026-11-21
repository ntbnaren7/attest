"""Specialists behind typed interfaces. The coordinator (voice or UI) reaches them only through
handoff(), which checks the allowed action, scopes everything to the operator's merchant, and
writes the handoff and its structured result to the trace.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import timedelta

from .context import Ctx
from .db import iso, new_id, now
from .money import format_inr
from .policy import CONTACT, Decision, decide, load_facts
from .providers.cashfree import ProviderError

# specialist -> actions it may perform. Nothing here moves money.
ALLOWED = {
    "evidence": {"check_payment_reference", "refresh_account", "refresh_link"},
    "reconciliation": {"allocate_receipts", "evaluate_case", "record_claim"},
    "outreach": {"propose_payment_link", "hold_outreach", "deliver_link"},
}


class HandoffDenied(Exception):
    pass


@dataclass
class HandoffResult:
    case_id: str
    specialist: str
    action: str
    status: str                     # ok | not_found | unavailable | conflict | denied | needs_input
    summary: str
    facts: dict = field(default_factory=dict)
    source_ids: list[str] = field(default_factory=list)
    checked_at: str = ""
    next_permitted_action: list[str] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def handoff(ctx: Ctx, merchant_id: str, case_id: str, specialist: str, action: str, *, actor: str,
            conversation_id: str | None = None, **kwargs) -> HandoffResult:
    if action not in ALLOWED.get(specialist, set()):
        ctx.db.trace(merchant_id, "denial", actor, account_id=case_id, target=specialist, allowed_action=action,
                     result={"reason": "action not allowed for specialist"}, conversation_id=conversation_id)
        raise HandoffDenied(f"{specialist} may not {action}")
    if not ctx.db.one("SELECT 1 FROM accounts WHERE id=? AND merchant_id=?", (case_id, merchant_id)):
        ctx.db.trace(merchant_id, "denial", actor, account_id=None, target=specialist, allowed_action=action,
                     input={"case_id": case_id}, result={"reason": "case not in this merchant's book"},
                     conversation_id=conversation_id)
        raise HandoffDenied("case not found for this merchant")
    fn = SPECIALISTS[(specialist, action)]
    res: HandoffResult = fn(ctx, merchant_id, case_id, **kwargs)
    ctx.db.trace(merchant_id, "handoff", actor, account_id=case_id, target=specialist, allowed_action=action,
                 input={k: v for k, v in kwargs.items()}, result=res.as_dict(), conversation_id=conversation_id)
    return res


# ---------------------------------------------------------------- evidence

def _record_payments(ctx: Ctx, merchant_id: str, case_id: str, payments, link_id: str | None) -> tuple[list[str], list[str]]:
    """Insert SUCCESS payments as receipts (idempotent on provider payment id). Returns (new_ids, all_success_ids)."""
    new, success = [], []
    for p in payments:
        if p.payment_status != "SUCCESS":
            continue  # PENDING / FAILED / USER_DROPPED never become receipts
        success.append(p.cf_payment_id)
        existing = ctx.db.one("SELECT * FROM receipts WHERE merchant_id=? AND provider='cashfree' AND provider_payment_id=?",
                              (merchant_id, p.cf_payment_id))
        if existing:
            if existing["account_id"] != case_id:
                ctx.db.x("UPDATE accounts SET evidence_conflict=? WHERE id=?",
                         (f"payment {p.cf_payment_id} is already credited to another account", case_id))
                ctx.db.bump_version(case_id)
            continue
        rid = new_id("rcpt")
        ctx.db.x("INSERT INTO receipts (id, merchant_id, account_id, provider, provider_payment_id, order_id, link_id,"
                 " amount_paise, payment_status, payment_time, verified_at, provenance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (rid, merchant_id, case_id, "cashfree", p.cf_payment_id, p.order_id, link_id, p.amount_paise,
                  p.payment_status, p.payment_time, iso(), p.provenance))
        new.append(rid)
    if new:
        ctx.db.bump_version(case_id)
    return new, success


def check_payment_reference(ctx: Ctx, merchant_id: str, case_id: str, reference: str,
                            claim_id: str | None = None) -> HandoffResult:
    cf = ctx.cashfree_for(merchant_id)
    at = iso()
    try:
        payments = cf.get_order_payments(reference)
    except ProviderError as e:
        status = "not_found" if e.kind == "not_found" else "unavailable"
        with ctx.db.tx():
            if claim_id:
                ctx.db.x("UPDATE claims SET status=?, status_reason=?, checked_at=? WHERE id=?",
                         ("unverified" if status == "not_found" else "provider_error",
                          f"provider has no order {reference}" if status == "not_found" else f"provider {e.kind}", at, claim_id))
            if status == "unavailable":
                ctx.db.x("UPDATE accounts SET provider_check_error=? WHERE id=?", (str(e)[:120], case_id))
            else:
                ctx.db.x("UPDATE accounts SET provider_checked_at=?, provider_check_error=NULL WHERE id=?", (at, case_id))
            ctx.db.bump_version(case_id)  # claim status or availability changed: material
        return HandoffResult(case_id, "evidence", "check_payment_reference", status,
                             f"No order {reference} at the provider" if status == "not_found"
                             else f"Provider unavailable ({e.kind}); nothing concluded",
                             facts={"reference": reference, "provenance": cf.provenance}, checked_at=at,
                             next_permitted_action=["request_reference"] if status == "not_found" else ["retry_later"])
    with ctx.db.tx():
        new, success = _record_payments(ctx, merchant_id, case_id, payments, None)
        statuses = sorted({p.payment_status for p in payments})
        if claim_id:
            before = ctx.db.one("SELECT status FROM claims WHERE id=?", (claim_id,))["status"]
            if success:
                ctx.db.x("UPDATE claims SET status='verified', status_reason=?, checked_at=? WHERE id=?",
                         (f"provider shows SUCCESS for {reference}", at, claim_id))
            else:
                ctx.db.x("UPDATE claims SET status='unverified', status_reason=?, checked_at=? WHERE id=?",
                         (f"order {reference} has no successful payment (statuses: {', '.join(statuses) or 'none'})", at, claim_id))
            if before != ("verified" if success else "unverified"):
                ctx.db.bump_version(case_id)
        prev_err = ctx.db.one("SELECT provider_check_error FROM accounts WHERE id=?", (case_id,))["provider_check_error"]
        ctx.db.x("UPDATE accounts SET provider_checked_at=?, provider_check_error=NULL WHERE id=?", (at, case_id))
        if prev_err:
            ctx.db.bump_version(case_id)
    total = sum(p.amount_paise for p in payments if p.payment_status == "SUCCESS")
    return HandoffResult(
        case_id, "evidence", "check_payment_reference", "ok",
        (f"Verified {format_inr(total)} received on {reference}" if success
         else f"Order {reference} exists but has no successful payment ({', '.join(statuses) or 'no attempts'})"),
        facts={"reference": reference, "payment_statuses": statuses, "success_paise": total,
               "new_receipts": new, "provenance": cf.provenance},
        source_ids=[f"cashfree:payment:{pid}" for pid in success] or [f"cashfree:order:{reference}"],
        checked_at=at, next_permitted_action=["allocate_receipts"] if success else ["request_reference"])


def refresh_link(ctx: Ctx, merchant_id: str, case_id: str, link_id: str) -> HandoffResult:
    """Re-read authoritative link, order and payment state. The only way link money enters the books."""
    cf = ctx.cashfree_for(merchant_id)
    link = ctx.db.one("SELECT * FROM payment_links WHERE link_id=? AND merchant_id=? AND account_id=?",
                      (link_id, merchant_id, case_id))
    if not link:
        return HandoffResult(case_id, "evidence", "refresh_link", "not_found", "Link not issued for this case")
    at = iso()
    try:
        state = cf.get_link(link_id)
        orders = cf.get_link_orders(link_id)
        payments = []
        for o in orders:
            if o.get("order_id"):
                payments.extend(cf.get_order_payments(o["order_id"]))
    except ProviderError as e:
        ctx.db.x("UPDATE accounts SET provider_check_error=? WHERE id=?", (f"{e.kind}", case_id))
        ctx.db.bump_version(case_id)
        return HandoffResult(case_id, "evidence", "refresh_link", "unavailable", f"Provider {e.kind}; state unchanged",
                             checked_at=at, next_permitted_action=["retry_later"])
    with ctx.db.tx():
        new, success = _record_payments(ctx, merchant_id, case_id, payments, link_id)
        success_total = sum(p.amount_paise for p in payments if p.payment_status == "SUCCESS")
        conflict = None
        if success_total != state.amount_paid_paise:
            conflict = (f"link {link_id} reports {format_inr(state.amount_paid_paise)} paid but successful payments "
                        f"total {format_inr(success_total)}")
        changed = (state.status != link["status"]) or (state.amount_paid_paise != link["amount_paid_paise"])
        ctx.db.x("UPDATE payment_links SET status=?, amount_paid_paise=?, last_checked_at=?, raw_json=? WHERE link_id=?",
                 (state.status, state.amount_paid_paise, at, json.dumps(state.raw, default=str)[:4000], link_id))
        prev = ctx.db.one("SELECT provider_check_error, evidence_conflict FROM accounts WHERE id=?", (case_id,))
        ctx.db.x("UPDATE accounts SET provider_checked_at=?, provider_check_error=NULL, evidence_conflict=? WHERE id=?",
                 (at, conflict, case_id))
        if changed or new or conflict != prev["evidence_conflict"] or prev["provider_check_error"]:
            ctx.db.bump_version(case_id)
    return HandoffResult(
        case_id, "evidence", "refresh_link", "conflict" if conflict else "ok",
        conflict or f"Link {state.status}: {format_inr(state.amount_paid_paise)} of {format_inr(state.amount_paise)} paid",
        facts={"link_status": state.status, "amount_paid_paise": state.amount_paid_paise,
               "amount_paise": state.amount_paise, "new_receipts": new, "provenance": state.provenance},
        source_ids=[f"cashfree:link:{link_id}"] + [f"cashfree:payment:{p}" for p in success], checked_at=at,
        next_permitted_action=["allocate_receipts"] if new else [])


def refresh_account(ctx: Ctx, merchant_id: str, case_id: str) -> HandoffResult:
    results = []
    for l in ctx.db.all("SELECT link_id FROM payment_links WHERE account_id=? AND merchant_id=? AND status IN "
                        "('ACTIVE','PARTIALLY_PAID')", (case_id, merchant_id)):
        results.append(refresh_link(ctx, merchant_id, case_id, l["link_id"]).as_dict())
    for c in ctx.db.all("SELECT * FROM claims WHERE account_id=? AND merchant_id=? AND kind='paid' AND reference IS NOT NULL"
                        " AND status IN ('open','provider_error','unverified')", (case_id, merchant_id)):
        results.append(check_payment_reference(ctx, merchant_id, case_id, c["reference"], claim_id=c["id"]).as_dict())
    if not results:
        ctx.db.x("UPDATE accounts SET provider_checked_at=?, provider_check_error=NULL WHERE id=?", (iso(), case_id))
    allocate_receipts(ctx, merchant_id, case_id)
    status = "unavailable" if any(r["status"] == "unavailable" for r in results) else "ok"
    return HandoffResult(case_id, "evidence", "refresh_account", status,
                         f"Re-checked {len(results)} provider record(s)", facts={"checks": results}, checked_at=iso())


# ---------------------------------------------------------------- reconciliation

def allocate_receipts(ctx: Ctx, merchant_id: str, case_id: str) -> HandoffResult:
    """Apply verified receipts to dues only when the target is unambiguous. Never by amount alone."""
    f = load_facts(ctx.db, merchant_id, case_id)
    made = []
    with ctx.db.tx():
        open_by_due = {d["id"]: d["amount_paise"] for d in f.dues}
        for a in f.allocations:
            if a["due_id"] in open_by_due:
                open_by_due[a["due_id"]] -= a["amount_paise"]
        credit_left = f.credit_total
        for d in f.dues:  # approved credits reduce oldest open dues for allocation purposes
            take = min(credit_left, open_by_due[d["id"]])
            open_by_due[d["id"]] -= take
            credit_left -= take
        for r in f.unallocated_receipts:
            amount = r["unallocated_paise"]
            open_dues = [d for d in f.dues if open_by_due[d["id"]] > 0]
            plan, basis = [], None
            ref_due = next((d for d in f.dues if d.get("payment_ref") and d["payment_ref"] == r["order_id"]), None)
            if ref_due:
                plan, basis = [(ref_due, min(amount, open_by_due[ref_due["id"]]))], f"payment reference {r['order_id']} on invoice {ref_due['external_ref']}"
            elif r.get("link_id"):
                # Our link was issued for the account's verified remainder: oldest open item first.
                rem = amount
                for d in open_dues:
                    take = min(rem, open_by_due[d["id"]])
                    if take > 0:
                        plan.append((d, take))
                        rem -= take
                basis = f"payment link {r['link_id']} issued for the account remainder"
            elif len(open_dues) == 1:
                plan, basis = [(open_dues[0], min(amount, open_by_due[open_dues[0]["id"]]))], "only one open item on the account"
            elif open_dues and amount == sum(open_by_due[d["id"]] for d in open_dues):
                plan, basis = [(d, open_by_due[d["id"]]) for d in open_dues], "receipt equals the total of all open items"
            else:
                continue  # ambiguous: stays unallocated -> REVIEW
            for d, amt in plan:
                if amt <= 0:
                    continue
                ctx.db.x("INSERT OR IGNORE INTO allocations (id, merchant_id, receipt_id, due_id, amount_paise, basis, created_at)"
                         " VALUES (?,?,?,?,?,?,?)", (new_id("alloc"), merchant_id, r["id"], d["id"], amt, basis, iso()))
                open_by_due[d["id"]] -= amt
                made.append({"receipt": r["provider_payment_id"], "invoice": d["external_ref"], "amount_paise": amt, "basis": basis})
        if made:
            ctx.db.bump_version(case_id)
        _settle_promises(ctx, merchant_id, case_id)
    return HandoffResult(case_id, "reconciliation", "allocate_receipts", "ok",
                         f"{len(made)} allocation(s) proposed" if made else "Nothing new to allocate",
                         facts={"allocations": made, "ledger_posting": "proposed (fixture ledger; no accounting connector)"},
                         checked_at=iso())


def _settle_promises(ctx: Ctx, merchant_id: str, case_id: str) -> None:
    f = load_facts(ctx.db, merchant_id, case_id)
    for p in [c for c in f.claims if c["kind"] == "promise" and c["status"] == "open"]:
        after = sum(r["amount_paise"] for r in f.receipts if r["verified_at"] >= p["created_at"])
        if f.outstanding == 0 or (p.get("amount_paise") and after >= p["amount_paise"]):
            ctx.db.x("UPDATE claims SET status='kept', status_reason=?, checked_at=? WHERE id=?",
                     (f"verified receipts of {format_inr(after)} after the promise", iso(), p["id"]))


def evaluate_case(ctx: Ctx, merchant_id: str, case_id: str) -> HandoffResult:
    d = evaluate(ctx, merchant_id, case_id)
    return HandoffResult(case_id, "reconciliation", "evaluate_case", "ok", d.reason, facts=d.as_dict(),
                         checked_at=iso(), next_permitted_action=d.next_actions)


def evaluate(ctx: Ctx, merchant_id: str, case_id: str) -> Decision:
    mode = ctx.db.one("SELECT mode FROM merchants WHERE id=?", (merchant_id,))["mode"]
    f = load_facts(ctx.db, merchant_id, case_id)
    s = ctx.settings
    return decide(f, now(), mode, stale_minutes=s.stale_minutes,
                  call_window=(s.call_window_start_hour, s.call_window_end_hour))


def record_claim(ctx: Ctx, merchant_id: str, case_id: str, kind: str, source: str, text: str = "",
                 amount_paise: int | None = None, reference: str | None = None,
                 promised_date: str | None = None) -> HandoffResult:
    cid = new_id("clm")
    with ctx.db.tx():
        ctx.db.x("INSERT INTO claims (id, merchant_id, account_id, kind, amount_paise, reference, promised_date, text,"
                 " source, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, merchant_id, case_id, kind, amount_paise, reference, promised_date, (text or "")[:500], source,
                  "open", iso()))
        if kind == "opt_out":
            ctx.db.x("UPDATE accounts SET do_not_contact=1 WHERE id=?", (case_id,))
        if kind == "paid" and not reference:
            ctx.db.x("UPDATE claims SET status='unverified', status_reason='no payment reference given', checked_at=? WHERE id=?",
                     (iso(), cid))
        # A new promise supersedes earlier open ones.
        if kind == "promise":
            ctx.db.x("UPDATE claims SET status='superseded' WHERE account_id=? AND kind='promise' AND status='open' AND id<>?",
                     (case_id, cid))
        ctx.db.bump_version(case_id)
    return HandoffResult(case_id, "reconciliation", "record_claim", "ok",
                         f"Recorded {kind} claim from {source} (a claim, not a fact)",
                         facts={"claim_id": cid, "kind": kind, "amount_paise": amount_paise, "reference": reference,
                                "promised_date": promised_date}, source_ids=[f"claim:{cid}"], checked_at=iso(),
                         next_permitted_action=["check_payment_reference"] if kind == "paid" and reference else [])


# ---------------------------------------------------------------- outreach

def propose_payment_link(ctx: Ctx, merchant_id: str, case_id: str, requested_by: str, requested_via: str,
                         spoken_amount_paise: int | None = None) -> HandoffResult:
    """Prepare an approval request for the verified collectible amount. Never creates the link itself."""
    from . import approvals
    d = evaluate(ctx, merchant_id, case_id)
    if d.decision != CONTACT or "request_payment_link" not in d.next_actions:
        return HandoffResult(case_id, "outreach", "propose_payment_link", "denied",
                             f"Not eligible for a payment request: {d.reason}", facts={"decision": d.decision, "code": d.code},
                             checked_at=iso(), next_permitted_action=d.next_actions)
    note = None
    if spoken_amount_paise is not None and spoken_amount_paise != d.collectible_paise:
        note = (f"Spoken amount {format_inr(spoken_amount_paise)} differs from the verified amount due "
                f"{format_inr(d.collectible_paise)}; the request uses the verified amount.")
    appr = approvals.request(ctx, merchant_id, case_id, "create_payment_link", requested_by, requested_via)
    return HandoffResult(case_id, "outreach", "propose_payment_link", "needs_input",
                         "Approval request created; an authenticated operator must approve it on screen.",
                         facts={"approval": appr, "note": note}, source_ids=[f"approval:{appr['id']}"], checked_at=iso(),
                         next_permitted_action=["approve_on_screen"])


def hold_outreach(ctx: Ctx, merchant_id: str, case_id: str, reason: str) -> HandoffResult:
    key = f"hold:{case_id}:{ctx.db.one('SELECT version FROM accounts WHERE id=?', (case_id,))['version']}"
    ctx.db.x("INSERT OR IGNORE INTO outbox (id, merchant_id, account_id, action, idem_key, channel, status, request_json, created_at)"
             " VALUES (?,?,?,?,?,?,?,?,?)", (new_id("ob"), merchant_id, case_id, "hold", key, "internal", "recorded",
                                              json.dumps({"reason": reason}), iso()))
    return HandoffResult(case_id, "outreach", "hold_outreach", "ok", f"Outreach held: {reason}", checked_at=iso())


def email_template_fields(p: dict) -> dict:
    """Pre-written values for Relay's Cashfree Communication template `merchant.generalTextEmailCustom`
    (variables: subject, title, shortMessage, upperBodyMessage, lowerBodyMessage). Mapped 1:1 in Relay."""
    lower = (f"Test recipient {p['recipient']}, language {p['language']}. Send once; do not create another link. "
             + p.get("disclosure", "")).strip()
    return {
        # Relay's Data Selector exposes `output › data`; one readable string there carries the whole case.
        "data": (f"Case {p['account_ref']}: {p['approved_amount_inr']} verified, undisputed and due. "
                 f"An operator approved one payment link on screen: {p['link_url']} . {lower}"),
        "subject": f"[TEST] Payment link approved: {p['account_ref']} {p['approved_amount_inr']}",
        "title": "Approved payment link (synthetic test data)",
        "shortMessage": f"{p['account_ref']}: {p['approved_amount_inr']} verified due",
        "upperBodyMessage": (f"Case {p['account_ref']} has a verified, undisputed amount of {p['approved_amount_inr']}. "
                             f"An operator approved one payment link on screen: {p['link_url']}"),
        "lowerBodyMessage": lower,
    }


def deliver_link(ctx: Ctx, merchant_id: str, case_id: str, link_id: str) -> HandoffResult:
    """Send the approved link through exactly one path (Relay webhook, else explicit manual step)."""
    link = ctx.db.one("SELECT * FROM payment_links WHERE link_id=? AND merchant_id=? AND account_id=?",
                      (link_id, merchant_id, case_id))
    acct = ctx.db.one("SELECT * FROM accounts WHERE id=?", (case_id,))
    mode = ctx.db.one("SELECT mode FROM merchants WHERE id=?", (merchant_id,))["mode"]
    idem = f"deliver:{link_id}"
    if ctx.db.one("SELECT 1 FROM outbox WHERE idem_key=?", (idem,)):
        return HandoffResult(case_id, "outreach", "deliver_link", "ok", "Already delivered once; not sending again",
                             checked_at=iso())
    s = ctx.settings
    packet = {
        "case_id": case_id, "account_ref": acct["external_ref"], "link_id": link_id, "link_url": link["link_url"],
        "approved_amount_inr": format_inr(link["amount_paise"]), "amount_paise": link["amount_paise"],
        "recipient": acct["phone"], "language": acct["language"] or "en-IN", "allowed_action": "send_payment_link_once",
        "test_mode": True,
    }
    if mode == "borrower":
        packet["disclosure"] = (f"This call/message is from {s.lender_display_name} about your loan account and may be "
                                f"recorded. For complaints: {s.grievance_contact}.")
        packet["contact_window"] = f"{s.call_window_start_hour}:00-{s.call_window_end_hour}:00 IST"
    packet.update(email_template_fields(packet))
    res = ctx.relay.deliver(packet, idem)
    ctx.db.x("INSERT INTO outbox (id, merchant_id, account_id, action, idem_key, channel, status, request_json, response_json, created_at)"
             " VALUES (?,?,?,?,?,?,?,?,?,?)", (new_id("ob"), merchant_id, case_id, "deliver_link", idem,
                                                f"relay_{res['mode']}", res["status"], json.dumps(packet, ensure_ascii=False),
                                                json.dumps(res, ensure_ascii=False, default=str), iso()))
    return HandoffResult(case_id, "outreach", "deliver_link", "ok" if res["status"] != "failed" else "unavailable",
                         {"awaiting_operator": "Relay delivery is a manual step for this account (packet shown to operator)",
                          "accepted": "Relay inbound webhook accepted the packet",
                          "failed": "Relay delivery failed; nothing else was sent"}[res["status"]],
                         facts=res, checked_at=iso())


SPECIALISTS = {
    ("evidence", "check_payment_reference"): check_payment_reference,
    ("evidence", "refresh_account"): refresh_account,
    ("evidence", "refresh_link"): refresh_link,
    ("reconciliation", "allocate_receipts"): allocate_receipts,
    ("reconciliation", "evaluate_case"): evaluate_case,
    ("reconciliation", "record_claim"): record_claim,
    ("outreach", "propose_payment_link"): propose_payment_link,
    ("outreach", "hold_outreach"): hold_outreach,
    ("outreach", "deliver_link"): deliver_link,
}

"""Queue policy. Pure functions of recorded facts and the clock: no model, no I/O.

Facts are kept distinct:
  receipts      provider-verified successful payments (money received)
  allocations   which invoice/instalment a receipt pays (ledger application)
  credits       approved credit notes
  claims        statements (paid / promise / deduction / hardship / opt-out). Never move a balance.

Prioritization uses ONLY the allow-listed behavioural features below. Name, language, area,
pincode, gender, religion, caste and any similar column never reach score().
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .db import IST, DB, parse_iso
from .money import format_inr

SCORING_FEATURES = ("days_past_due", "on_time_payments_6m", "missed_payments_6m", "broken_promises")

CONTACT, HOLD, REVIEW, CLOSED = "CONTACT", "HOLD", "REVIEW", "CLOSED"


@dataclass
class AccountFacts:
    account: dict
    dues: list[dict]
    credits: list[dict]
    claims: list[dict]
    receipts: list[dict]
    allocations: list[dict]
    links: list[dict]

    @property
    def features(self) -> dict:
        raw = json.loads(self.account.get("features_json") or "{}")
        return {k: raw[k] for k in SCORING_FEATURES if k in raw}

    @property
    def gross_due(self) -> int:
        return sum(d["amount_paise"] for d in self.dues)

    @property
    def allocated(self) -> int:
        due_ids = {d["id"] for d in self.dues}
        return sum(a["amount_paise"] for a in self.allocations if a["due_id"] in due_ids)

    @property
    def credit_total(self) -> int:
        return sum(c["amount_paise"] for c in self.credits)

    @property
    def received(self) -> int:
        return sum(r["amount_paise"] for r in self.receipts)

    @property
    def unallocated_receipts(self) -> list[dict]:
        by_r: dict[str, int] = {}
        for a in self.allocations:
            by_r[a["receipt_id"]] = by_r.get(a["receipt_id"], 0) + a["amount_paise"]
        return [dict(r, unallocated_paise=r["amount_paise"] - by_r.get(r["id"], 0))
                for r in self.receipts if r["amount_paise"] - by_r.get(r["id"], 0) > 0]

    @property
    def outstanding(self) -> int:
        """Amount still due on the ledger after verified, allocated receipts and approved credits."""
        return max(self.gross_due - self.allocated - self.credit_total, 0)

    def open_claims(self, kind: str) -> list[dict]:
        return [c for c in self.claims if c["kind"] == kind and c["status"] in ("open", "unverified", "provider_error")]


def load_facts(db: DB, merchant_id: str, account_id: str) -> AccountFacts | None:
    acct = db.one("SELECT * FROM accounts WHERE id=? AND merchant_id=?", (account_id, merchant_id))
    if not acct:
        return None
    q = lambda sql: db.all(sql, (account_id, merchant_id))
    return AccountFacts(
        account=acct,
        dues=q("SELECT * FROM dues WHERE account_id=? AND merchant_id=? AND superseded=0 ORDER BY due_date, external_ref"),
        credits=q("SELECT * FROM credits WHERE account_id=? AND merchant_id=?"),
        claims=q("SELECT * FROM claims WHERE account_id=? AND merchant_id=? ORDER BY created_at"),
        receipts=q("SELECT * FROM receipts WHERE account_id=? AND merchant_id=? ORDER BY verified_at"),
        allocations=db.all("SELECT a.* FROM allocations a JOIN receipts r ON r.id=a.receipt_id "
                           "WHERE r.account_id=? AND a.merchant_id=?", (account_id, merchant_id)),
        links=q("SELECT * FROM payment_links WHERE account_id=? AND merchant_id=? ORDER BY created_at DESC"),
    )


@dataclass
class Decision:
    decision: str
    code: str
    reason: str
    outstanding_paise: int
    collectible_paise: int
    disputed_paise: int = 0
    unallocated_paise: int = 0
    score: float = 0.0
    factors: list[str] = field(default_factory=list)
    contact_allowed_now: bool = False
    next_actions: list[str] = field(default_factory=list)
    route_to: str | None = None
    offered_options: list[str] = field(default_factory=list)
    verification_stale: bool = False
    last_verified_at: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def in_call_window(at: datetime, start_hour: int, end_hour: int) -> bool:
    local = at.astimezone(IST)
    return start_hour <= local.hour < end_hour


def score(features: dict, collectible_paise: int, salary_day: int | None, recent_partial: bool,
          today: date) -> tuple[float, list[str]]:
    """Likelihood-to-pay-if-asked score (0-100) with readable factors. Inputs are allow-listed only."""
    assert set(features) <= set(SCORING_FEATURES), "non-allow-listed feature reached the scorer"
    s, factors = 30.0, []
    on_time = int(features.get("on_time_payments_6m", 0) or 0)
    missed = int(features.get("missed_payments_6m", 0) or 0)
    if on_time + missed:
        ratio = on_time / (on_time + missed)
        s += 30 * ratio - 10
        factors.append(f"paid on time {on_time} of last {on_time + missed} months")
    dpd = int(features.get("days_past_due", 0) or 0)
    if 0 < dpd <= 30:
        s += 15
        factors.append(f"only {dpd} days overdue (often a missed reminder)")
    elif 30 < dpd <= 60:
        s += 6
        factors.append(f"{dpd} days overdue")
    elif dpd > 60:
        factors.append(f"{dpd} days overdue (long overdue lowers the chance a call alone works)")
    if salary_day:
        days_to = (salary_day - today.day) % 31
        if days_to <= 3 or (today.day - salary_day) % 31 <= 2:
            s += 20
            factors.append(f"note says income arrives on the {salary_day}th, within a few days of today")
        else:
            s -= 5
            factors.append(f"note says income arrives on the {salary_day}th; better to call nearer that date")
    if recent_partial:
        s += 12
        factors.append("made a part payment recently")
    bp = int(features.get("broken_promises", 0) or 0)
    if bp:
        s -= min(8 * bp, 24)
        factors.append(f"{bp} earlier promise(s) not kept")
    s = max(0.0, min(100.0, s))
    return round(s, 1), factors


def decide(f: AccountFacts, at: datetime, mode: str, *, stale_minutes: int = 30,
           call_window=(8, 19)) -> Decision:
    acct = f.account
    outstanding = f.outstanding
    today = at.astimezone(IST).date()
    checked = parse_iso(acct.get("provider_checked_at"))
    needs_provider = bool(f.links or any(d.get("payment_ref") for d in f.dues) or f.open_claims("paid"))
    stale = bool(needs_provider and (checked is None or at - checked > timedelta(minutes=stale_minutes)))
    last_verified = max(filter(None, [acct.get("provider_checked_at"), acct.get("ledger_read_at")]), default=None)
    base = dict(outstanding_paise=outstanding, collectible_paise=0, verification_stale=stale, last_verified_at=last_verified)

    def mk(decision, code, reason, **kw):
        d = Decision(decision=decision, code=code, reason=reason, **{**base, **kw})
        if decision in (HOLD, REVIEW) and not d.next_actions:
            d.next_actions = ["refresh_evidence", "route_to_owner"]
        return d

    owner = "commercial owner (sales/accounts)" if mode == "merchant" else "collections supervisor"

    if acct.get("do_not_contact"):
        return mk(HOLD, "OPT_OUT", "Asked us not to call this number. No outreach; a person must decide the next "
                  "step through an approved channel.", route_to="collections supervisor")
    if acct.get("evidence_conflict"):
        return mk(REVIEW, "EVIDENCE_CONFLICT", f"Payment records disagree: {acct['evidence_conflict']}. Not chasing "
                  "until a person checks.", route_to=owner)
    if acct.get("provider_check_error") and needs_provider:
        return mk(REVIEW, "PROVIDER_UNAVAILABLE", "Could not reach the payment provider to confirm status "
                  f"({acct['provider_check_error']}). Not contacting on unverified data; will retry.")

    paid_claims = f.open_claims("paid")
    if paid_claims:
        c = paid_claims[-1]
        said = f" {format_inr(c['amount_paise'])}" if c.get("amount_paise") else ""
        ref = f" (reference {c['reference']})" if c.get("reference") else " (no reference given)"
        if c["status"] == "open":
            return mk(REVIEW, "PAID_CLAIM_CHECKING", f"Says they already paid{said}{ref}. Checking with the provider "
                      "before any reminder.")
        found = ("gave no payment reference, so there is nothing to verify yet" if not c.get("reference")
                 else f"no successful payment was found ({c.get('status_reason') or 'not found'})")
        return mk(REVIEW, "PAID_CLAIM_UNVERIFIED", f"Says they already paid{said}{ref}, but {found}. Holding "
                  "reminders; ask for the payment reference or receipt.", next_actions=["request_reference", "route_to_owner"])

    unalloc = f.unallocated_receipts
    unalloc_total = sum(r["unallocated_paise"] for r in unalloc)
    disputes = f.open_claims("deduction") + f.open_claims("dispute")
    if disputes and outstanding > 0:
        disputed = sum(c.get("amount_paise") or 0 for c in disputes)
        what = f"{format_inr(disputed)} " if disputed else ""
        extra = (f" A verified receipt of {format_inr(unalloc_total)} is recorded but not yet applied to specific "
                 "invoices.") if unalloc_total else ""
        return mk(REVIEW, "DISPUTED_DEDUCTION", f"Claims a {what}deduction/dispute that has no approved credit note. "
                  f"{format_inr(outstanding - unalloc_total if unalloc_total < outstanding else outstanding)} remains "
                  f"open; no collection request while it is reviewed.{extra}",
                  disputed_paise=disputed, unallocated_paise=unalloc_total, route_to=owner)

    if unalloc:
        total = unalloc_total
        if outstanding == 0:
            return mk(REVIEW, "OVERPAYMENT", f"Received {format_inr(total)} more than is due. Not chasing; a person "
                      "decides credit or refund.", route_to=owner)
        return mk(REVIEW, "UNALLOCATED_RECEIPT", f"A verified payment of {format_inr(total)} has not been matched to "
                  "a specific invoice (it could cover several). Not chasing until allocation is confirmed.",
                  route_to=owner, next_actions=["request_remittance", "route_to_owner"])

    if mode == "borrower" and f.open_claims("hardship") and outstanding > 0:
        return mk(REVIEW, "HARDSHIP", "Reported hardship. Collections calls are paused; a person offers options.",
                  route_to="hardship desk",
                  offered_options=["Part-payment link with a small minimum first amount (after review)",
                                   "New date aligned to the borrower's income date",
                                   "Restructuring request to the credit officer"])

    if outstanding == 0:
        if f.receipts:
            last = f.receipts[-1]
            return mk(CLOSED, "PAID_VERIFIED", f"Paid in full. Verified payment {last['provider_payment_id']} of "
                      f"{format_inr(last['amount_paise'])} ({last['provenance']}). Not chasing this account.",
                      next_actions=["propose_ledger_posting"])
        if f.credits:
            return mk(CLOSED, "SETTLED_BY_CREDIT", "Balance cleared by an approved credit note. Not chasing.")
        return mk(CLOSED, "NOTHING_DUE", "Nothing is due on the ledger.")

    promises = [c for c in f.claims if c["kind"] == "promise" and c["status"] == "open"]
    if promises:
        p = promises[-1]
        pd = date.fromisoformat(p["promised_date"]) if p.get("promised_date") else None
        amt = f"{format_inr(p['amount_paise'])} " if p.get("amount_paise") else ""
        if pd and pd >= today:
            return mk(HOLD, "PROMISE_ACTIVE", f"Promised to pay {amt}by {pd.isoformat()}. Waiting until then; "
                      f"{format_inr(outstanding)} still due.", next_actions=["refresh_evidence"])
        if pd and pd < today:
            return mk(REVIEW, "PROMISE_LAPSED", f"Promise to pay {amt}by {pd.isoformat()} has passed without a "
                      f"verified payment. {format_inr(outstanding)} still due; review before contacting again.",
                      next_actions=["confirm_follow_up", "route_to_owner"])

    active = [l for l in f.links if l["status"] in ("ACTIVE", "PARTIALLY_PAID")]
    salary_day = next((int(c["text"]) for c in f.claims if c["kind"] == "salary_date" and (c.get("text") or "").isdigit()), None)
    recent_partial = any(parse_iso(r["verified_at"]) and at - parse_iso(r["verified_at"]) < timedelta(days=14)
                         for r in f.receipts)
    s, factors = score(f.features, outstanding, salary_day, recent_partial, today)
    allowed_now = True
    window_note = ""
    if mode == "borrower" and not in_call_window(at, *call_window):
        allowed_now = False
        window_note = f" Outside the permitted calling window ({call_window[0]}:00-{call_window[1]}:00); queue for the next window."
    if active:
        l = active[0]
        if l["status"] == "PARTIALLY_PAID":
            return mk(CONTACT, "PARTIAL_PAID", f"Paid {format_inr(l['amount_paid_paise'])} of a "
                      f"{format_inr(l['amount_paise'])} link; {format_inr(outstanding)} still due. Remind about the "
                      f"same link, no new link.{window_note}", collectible_paise=outstanding, score=s,
                      factors=factors, contact_allowed_now=allowed_now, next_actions=["send_reminder"])
        return mk(HOLD, "LINK_PENDING", f"A {format_inr(l['amount_paise'])} payment link is already out and unpaid. "
                  "No second request.", next_actions=["refresh_evidence", "send_reminder"])

    why = "; ".join(factors) if factors else "no payment history in the file"
    return mk(CONTACT, "DUE_UNDISPUTED", f"{format_inr(outstanding)} verified due, no dispute or open claim. "
              f"Priority because: {why}.{window_note}", collectible_paise=outstanding, score=s, factors=factors,
              contact_allowed_now=allowed_now, next_actions=["request_payment_link"])

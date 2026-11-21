"""Ask: one entry point that sends a merchant's question to the right agent.

    route(question)            -> which agent, why, which IDs it found, what is still missing   (pure, testable)
    prepare(route_result)      -> verify the facts in Cashfree and build the agent's input      (reads sandbox)
    dispatch(prepared, ...)    -> run our reconciliation locally, or POST to the Relay agent's Inbound Webhook

Routing is deterministic (keyword evidence + ID patterns) so it is explainable and testable; Sarvam is only used to
translate non-English questions and as a tie-breaker when the rules are unsure. Before a Relay agent is called the
facts are checked against Cashfree (e.g. never send a "retry payment" link for an order that is already paid): this is
where reconciliation and the Relay agents meet.
"""
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request

# ───────────────────────────── agent registry ─────────────────────────────
AGENTS = {
    "reconciliation": dict(title="Reconciliation & Leakage (ours)", where="local", needs=[],
                           does="books vs Cashfree vs bank: unsettled/short credits, fee & GST leakage, duplicate refunds/payouts"),
    "payment_recovery": dict(title="Failed Payment Recovery (Relay)", where="relay", env="RELAY_WH_PAYMENT_RECOVERY", needs=["order_id"],
                             does="classifies a failed payment and sends the customer a retry payment link"),
    "refund_shield": dict(title="Refunds Shield (Relay)", where="relay", env="RELAY_WH_REFUND_SHIELD", needs=["order_id"],
                          does="reviews a refund request against policy and history, flags risky refunds"),
    "dispute_defender": dict(title="Dispute Defender (Relay)", where="relay", env="RELAY_WH_DISPUTE_DEFENDER", needs=["order_id|dispute_id"],
                             does="builds and submits evidence for a chargeback / dispute"),
    "subscription_dunning": dict(title="Subscription Dunning (Relay)", where="relay", env="RELAY_WH_SUBSCRIPTION_DUNNING", needs=["subscription_id"],
                                 does="recovers a failed subscription renewal"),
    "cod_guard": dict(title="COD Guard (Relay)", where="relay", env="RELAY_WH_COD_GUARD", needs=["order_id"],
                      does="confirms a cash-on-delivery order and address with the customer before shipping"),
    "loan_recovery": dict(title="Loan Recovery (Relay)", where="relay", env="RELAY_WH_LOAN_RECOVERY", needs=["loan_id|phone"],
                          does="reminds a borrower about overdue EMI / loan dues and records a repayment plan"),
    "cart_recovery": dict(title="Abandoned Cart Recovery (Relay)", where="relay", env="RELAY_WH_CART_RECOVERY", needs=["order_id"],
                          does="reminds a customer who left checkout without paying"),
}

# keyword evidence per agent: (regex, weight). Specific phrases outweigh generic words.
_K = {
    "reconciliation": [(r"reconcil\w*|\brecon\b|\bmatch\w* (?:my |the )?(?:\w+ )*(?:report|statement|settlement|books)", 3), (r"\bsettle(?:d|ment|ments)?\b", 2), (r"\bunsettled\b", 3),
                       (r"\bbank (?:credit|statement|account)|\bcurrent account\b|\bcredit(?:ed)? (?:short|missing)|short credit|missing credit", 3),
                       (r"(?:never|not|didn'?t|did not) (?:reach|receive|hit|get|arrive)\w*|received less|full amount|hit my account", 3),
                       (r"\bleak\w*|\bfee(?:s)?\b|\bgst\b|\bmdr\b|overcharg\w*|\bdeduction\w*\b|\bcharges?\b", 2),
                       (r"\bduplicate (?:payout|refund)s?\b|\bpayouts? (?:\w+ )*(?:twice|double)|paid (?:\w+ )*twice", 3),
                       (r"\bmismatch\w*|\btie[- ]?out\b|\bbooks?\b|\bledger\b|\bkhata\b", 2), (r"\bat (?:stake|risk)\b|where is my money|money (?:lost|missing)", 2)],
    "payment_recovery": [(r"\bfailed payment|payment (?:has )?failed|payment fail\w*|\bdeclined\b|transaction failed|(?:payment|attempt|transaction) (?:didn'?t|did not) go through|\bfailed\b", 4),
                         (r"\bretry\b|\bpay(?:ment)? link\b|\bnew link\b|\brecover\w*\b|pay again|try again", 2),
                         (r"\bcustomer (?:couldn'?t|could not|didn'?t) pay\b", 3)],
    "refund_shield": [(r"\brefund\w*\b|money back|reimburs\w*|\breturn (?:the )?(?:item|product|order)\b", 3),
                      (r"\bri?sky\b|\babus\w*|\bfraud\w*\b|\bsuspicious\b|\bgenuine\b|\bshould i (?:approve|refund)\b", 2), (r"\breturn request\b", 2)],
    "dispute_defender": [(r"\bdispute\w*\b|\bchargeback\w*\b", 4), (r"\bevidence\b|\bissuer\b|\bcontest\b", 2)],
    "subscription_dunning": [(r"\bsubscription\w*\b|\bmandate\b|\bautopay\b|\brenewal\b|\brecurring\b|\bplan renewal\b", 4), (r"\bcard expired\b|\bbilling\b", 1)],
    "cod_guard": [(r"\bcod\b|cash on delivery", 4), (r"\brto\b|\bconfirm (?:the )?(?:order|address)\b|\bfake order\b", 2)],
    "loan_recovery": [(r"\bloan\w*\b|\bemi\w*\b|\bborrower\w*\b|\binstal+ments?\b", 4), (r"\boverdue\b|\bdues?\b|\brepay\w*\b", 2)],
    "cart_recovery": [(r"\babandon\w*\b|\bcart\b|\bbasket\b|left (?:the )?checkout|without (?:checkout|checking out|paying)|didn'?t complete (?:the )?(?:checkout|purchase)", 4)],
}

_ID = {
    "order_id": r"\b(?:order[\s#:]*)?((?:seed|CFPay|order|ord)_[A-Za-z0-9_]+)\b|\border[\s#:]+([A-Za-z0-9_-]{4,})\b",
    "dispute_id": r"\bdispute[\s#:]+(\d{3,}|[A-Za-z0-9_-]{6,})\b",
    "subscription_id": r"\b(sub_[A-Za-z0-9_-]+)\b|\bsubscription[\s#:]+([A-Za-z0-9_-]{4,})\b",
    "loan_id": r"\b(LN[-_]?\d{3,}|loan_[A-Za-z0-9_-]+)\b|\bloan[\s#:]+([A-Za-z0-9_-]{3,})\b",
    "phone": r"(?<!\d)(\+?91[\s-]?)?([6-9]\d{9})(?!\d)",
    "amount": r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)",
    "run": r"\b(20\d{6}_\d{6})\b",
}
_STOP = {"for", "the", "is", "of", "to", "a", "an", "my", "this", "that", "please", "id", "number", "no"}


def extract_ids(q):
    out = {}
    for k, pat in _ID.items():
        for m in re.finditer(pat, q, re.I):
            g = [x for x in m.groups() if x]
            v = g[-1] if g else None
            if k == "phone" and m:
                v = m.group(2)
            if v and v.lower() not in _STOP:
                out.setdefault(k, v.replace(",", "") if k == "amount" else v)
                break
    return out


def route(question, llm=None):
    """Pick the agent for a question. Returns {agent, confidence, scores, why, ids, missing, alternatives}."""
    q = question.strip()
    ql = q.lower()
    scores, why = {}, {}
    for a, rules in _K.items():
        hits = [(m.group(0), w) for pat, w in rules for m in [re.search(pat, ql)] if m]
        if hits:
            scores[a] = sum(w for _, w in hits)
            why[a] = [h for h, _ in hits]
    ids = extract_ids(q)
    # tie-breaks that encode domain knowledge, not luck
    if "refund_shield" in scores and re.search(r"duplicate refund|refund\w*\b[^.?!]*\b(?:twice|double|more than|in excess|over)\b|over-?refund", ql):
        # money already refunded wrongly is leakage (reconciliation), not a new refund request to vet
        scores["reconciliation"] = scores["refund_shield"] + 3; why.setdefault("reconciliation", []).append("refund leakage")
    if "subscription_dunning" in scores and ("subscription_id" in ids or "payment_recovery" in scores):
        scores["subscription_dunning"] += 3  # a failed *renewal* is dunning, not one-off recovery
    if "payment_recovery" in scores and "cart_recovery" in scores and not re.search(r"fail|declin", ql):
        scores["cart_recovery"] += 2
    if "loan_recovery" in scores and "payment_recovery" in scores and re.search(r"\bemi\b|loan", ql):
        scores["loan_recovery"] += 2
    if not scores:
        if llm:
            pick = _llm_route(q, llm)
            if pick:
                return _result(pick, {pick: 1}, {pick: ["chosen by Sarvam (no keyword match)"]}, ids, low=True)
        return dict(agent=None, confidence=0.0, scores={}, why={}, ids=ids, missing=[],
                    alternatives=[], message="I couldn't tell which agent this needs. Try mentioning the order, refund, dispute, settlement, subscription, COD order, loan or cart.")
    best = max(scores, key=scores.get)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    tie = len(ranked) > 1 and ranked[1][1] == ranked[0][1]
    if tie and llm:
        pick = _llm_route(q, llm, among=[a for a, s in ranked if s == ranked[0][1]])
        if pick:
            best = pick
    return _result(best, scores, why, ids, low=tie)


def _result(best, scores, why, ids, low=False):
    total = sum(scores.values()) or 1
    conf = round(scores[best] / total, 2) * (0.7 if low else 1.0)
    missing = [n for n in AGENTS[best]["needs"] if not any(alt in ids for alt in n.split("|"))]
    return dict(agent=best, title=AGENTS[best]["title"], confidence=round(conf, 2), scores=scores, why=why.get(best, []), ids=ids,
                missing=missing, alternatives=[a for a in sorted(scores, key=lambda a: -scores[a]) if a != best][:2])


def _llm_route(q, llm, among=None):
    opts = among or list(AGENTS)
    prompt = ("Pick the ONE agent that should handle this merchant question. Answer with the agent key only.\n"
              + "\n".join(f"- {a}: {AGENTS[a]['does']}" for a in opts) + f"\n\nQuestion: {q}\nAgent key:")
    try:
        ans = (llm(prompt) or "").strip().lower()
    except Exception:
        return None
    return next((a for a in opts if a in ans), None)


def to_english(question, sarvam=None):
    """Non-English (e.g. Hindi/Kannada) questions are translated with Sarvam first; IDs and numbers survive translation."""
    if not sarvam or re.fullmatch(r"[\x00-\x7F₹]*", question):
        return question, None
    try:
        r = sarvam.translate(question, target="en-IN", source="auto")
        return (r.get("translated_text") or question), r.get("source_language_code")
    except Exception:
        return question, None


# ───────────────────────────── verify facts, build the agent input ─────────────────────────────
def prepare(r, cfio=None, books=None):
    """Check the request against Cashfree (and the books) before any agent acts. Returns
    {ok, agent, payload, facts, refuse_reason}. `payload` mirrors Cashfree's webhook `data` object so the Relay
    templates' existing references (output.data.order / payment / error_details / customer_details) keep working."""
    a, ids = r["agent"], r["ids"]
    if r.get("missing"):
        return dict(ok=False, agent=a, refuse_reason=f"Need {', '.join(m.replace('|', ' or ') for m in r['missing'])} to run {AGENTS[a]['title']}.")
    if a == "reconciliation":
        return dict(ok=True, agent=a, payload=dict(run=ids.get("run")), facts={})
    if a in ("payment_recovery", "refund_shield", "cod_guard", "cart_recovery", "dispute_defender") and ids.get("order_id"):
        snap = cfio.order_snapshot(ids["order_id"])
        o = snap.get("order") or {}
        if not o or "_error" in o:
            return dict(ok=False, agent=a, refuse_reason=f"Order {ids['order_id']} was not found in Cashfree.")
        pays = snap["payments"]
        paid = [p for p in pays if p.get("payment_status") == "SUCCESS"]
        book = (books or {}).get(ids["order_id"]) or {}
        facts = dict(order_status=o.get("order_status"), order_amount=o.get("order_amount"), payments=[p.get("payment_status") for p in pays],
                     captured=round(sum(float(p.get("payment_amount") or 0) for p in paid), 2),
                     refunded=round(sum(float(x.get("refund_amount") or 0) for x in snap["refunds"] if x.get("refund_status") in ("SUCCESS", "PENDING", "ONHOLD")), 2),
                     books_status=book.get("ledger_status"), books_fulfilled=book.get("fulfilled"))
        base = dict(order={k: o.get(k) for k in ("order_id", "order_amount", "order_currency", "order_status", "order_tags")},
                    customer_details=o.get("customer_details") or {})
        last = pays[-1] if pays else {}
        if a == "payment_recovery":
            if paid:
                return dict(ok=False, agent=a, facts=facts, refuse_reason=f"Order {ids['order_id']} is already PAID in Cashfree (₹{facts['captured']:,.2f} captured): no retry link needed."
                            + (" Your books still show it unpaid, so update the books instead." if facts["books_status"] in ("UNPAID", "PENDING") else ""))
            if not pays or last.get("payment_status") not in ("FAILED", "USER_DROPPED", "CANCELLED"):
                return dict(ok=False, agent=a, facts=facts, refuse_reason=f"Order {ids['order_id']} has no failed payment to recover (payments: {facts['payments'] or 'none'}).")
            payload = dict(base, payment={k: last.get(k) for k in ("cf_payment_id", "payment_status", "payment_amount", "payment_currency", "payment_message", "payment_time", "payment_group")},
                           error_details=dict(last.get("error_details") or {}))
            if last.get("payment_status") == "USER_DROPPED" and not payload["error_details"].get("error_code"):
                payload["error_details"]["error_code"] = "USER_DROPPED"  # Cashfree leaves it empty; Relay's template branches on it
            return dict(ok=True, agent=a, payload=payload, facts=facts)
        if a == "refund_shield":
            want = float(ids.get("amount") or 0) or None
            if not paid:
                return dict(ok=False, agent=a, facts=facts, refuse_reason=f"Order {ids['order_id']} was never paid, so there is nothing to refund.")
            room = round(facts["captured"] - facts["refunded"], 2)
            if want and want > room + 0.005:
                return dict(ok=False, agent=a, facts=facts, refuse_reason=f"Refund of ₹{want:,.2f} exceeds what is left to refund (₹{room:,.2f} = captured ₹{facts['captured']:,.2f} − refunded ₹{facts['refunded']:,.2f}). Blocked as over-refund.")
            payload = dict(base, refund=dict(refund_amount=want or room, refundable=room, already_refunded=facts["refunded"],
                                             prior_refunds=len(snap["refunds"])), payment={k: paid[-1].get(k) for k in ("cf_payment_id", "payment_amount", "payment_time", "payment_group")})
            return dict(ok=True, agent=a, payload=payload, facts=facts)
        if a == "cart_recovery":
            if paid:
                return dict(ok=False, agent=a, facts=facts, refuse_reason=f"Order {ids['order_id']} is already paid: not an abandoned cart.")
            return dict(ok=True, agent=a, payload=dict(base, payment={k: last.get(k) for k in ("payment_status", "payment_time")} if last else {}), facts=facts)
        if a == "cod_guard":
            return dict(ok=True, agent=a, payload=base, facts=facts)
        if a == "dispute_defender":
            if not paid:
                return dict(ok=False, agent=a, facts=facts, refuse_reason=f"Order {ids['order_id']} has no successful payment, so it cannot have a chargeback.")
            return dict(ok=True, agent=a, payload=dict(base, payment={k: paid[-1].get(k) for k in ("cf_payment_id", "payment_amount", "payment_time", "bank_reference")},
                                                      dispute=dict(dispute_id=ids.get("dispute_id"))), facts=facts)
    # agents keyed by non-order ids (subscription, loan, dispute without order): pass the identifiers through
    return dict(ok=True, agent=a, payload={k: v for k, v in ids.items() if k != "run"}, facts={})


# ───────────────────────────── dispatch ─────────────────────────────
CALLS = {}  # call_id -> {agent, nonce, status, sent_at, payload, result}


def dispatch(p, http_post=None):
    """POST the verified payload to the Relay agent's Inbound Webhook. The call carries a single-use nonce that the agent
    echoes in its final HTTP step; /api/relay/agent-result only accepts results whose nonce matches an open call."""
    a = p["agent"]
    url = os.getenv(AGENTS[a]["env"]) if AGENTS[a]["where"] == "relay" else None
    if not url:
        return dict(ok=False, status="NOT_WIRED", reason=f"{AGENTS[a]['title']} is not connected yet (set {AGENTS[a].get('env')}).")
    call_id, nonce = f"call_{secrets.token_hex(6)}", secrets.token_hex(16)
    # Relay exposes the POSTed body as the trigger output; Cashfree's own events carry everything under "data", so
    # wrapping keeps the templates' `output.data.order / payment / error_details` references valid for real calls.
    body = {"data": dict(p["payload"], callback=dict(call_id=call_id, nonce=nonce, agent=a))}
    CALLS[call_id] = dict(agent=a, nonce=nonce, status="DISPATCHED", sent_at=time.time(), payload=p["payload"], result=None)
    code, text = (http_post or _post)(url, body)
    if code >= 300:
        CALLS[call_id]["status"] = "DISPATCH_FAILED"
        return dict(ok=False, status="DISPATCH_FAILED", call_id=call_id, http=code, reason=text[:200])
    return dict(ok=True, status="DISPATCHED", call_id=call_id)


def record_result(body, max_age_s=900):
    """Called by the Relay agent's final HTTP step (through the Lambda). Nonce-checked, single use, time-limited."""
    c = CALLS.get(body.get("call_id") or "")
    if not c or not secrets.compare_digest(str(body.get("nonce") or ""), c["nonce"]):
        return False, "unknown call or bad nonce"
    if c["status"] == "COMPLETED":
        return False, "result already recorded"
    if time.time() - c["sent_at"] > max_age_s:
        return False, "call expired"
    c.update(status="COMPLETED", result={k: v for k, v in body.items() if k not in ("nonce",)}, done_at=time.time())
    return True, "ok"


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode()[:500]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]
    except Exception as e:
        return 599, str(e)


# recon finding -> which Relay agent can act on it (used to offer "fix it" next steps from a reconciliation answer)
FINDING_TO_AGENT = {"FULFILLED_BUT_UNPAID": "payment_recovery", "CHARGEBACK_DEDUCTED": "dispute_defender", "CHARGEBACK_RISK": "dispute_defender",
                    "DUPLICATE_REFUND": "refund_shield", "OVER_REFUND": "refund_shield", "REFUND_NOT_IN_BOOKS": "refund_shield"}

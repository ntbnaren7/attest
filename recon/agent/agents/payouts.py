"""Payouts: audit executed payouts (stuck / reversed / failed) and decide the request queue
(sanctions screen, IFSC registry, duplicate check, Secure ID name-at-bank + guarded name match, ZEN policy)."""
from ..config import SANCTIONS_SCREENING, STUCK_PAYOUT_MINUTES
from ..lib.checks import ifsc_valid, name_match, sanctions_screen
from ..lib.dates import age_minutes
from ..lib.findings import Finding
from ..lib.money import inr, paise
from ..lib.rules import decide
from ..sources import cashfree as cfio


def _audit(reg, out):
    for p in reg:
        st, code = p["live"].get("status"), p["live"].get("status_code")
        age = age_minutes(p["live"].get("added_on"))
        if st in ("PENDING", "RECEIVED") and age is not None and age < STUCK_PAYOUT_MINUTES:
            out.append(Finding("payouts", "PAYOUT_IN_FLIGHT", p["transfer_id"], p["amount"], "info",
                               f"{inr(p['amount'])} to {p['payee']} {st} for {age:.0f} min (normal; flagged as stuck after {STUCK_PAYOUT_MINUTES} min).",
                               dict(payee=p["payee"], status_code=code, age_minutes=round(age, 1))))
        elif st == "PENDING":
            out.append(Finding("payouts", "STUCK_PAYOUT", p["transfer_id"], p["amount"], "high",
                               f"{inr(p['amount'])} to {p['payee']} stuck {st}/{code} for {age or 0:.0f} min. Do not re-send; check with Cashfree/bank.",
                               dict(payee=p["payee"], status_code=code, added_on=p["live"].get("added_on"), age_minutes=round(age or 0, 1)),
                               action="INVESTIGATE", priority=p["amount"], money_key=f"transfer:{p['transfer_id']}"))
        elif st == "REVERSED":
            out.append(Finding("payouts", "REVERSED_PAYOUT", p["transfer_id"], p["amount"], "medium",
                               f"{inr(p['amount'])} to {p['payee']} was returned by the beneficiary bank ({code}); funds are back in the wallet. Re-verify the account before paying again.",
                               dict(payee=p["payee"], status_code=code), action="REVERIFY_BENEFICIARY", priority=p["amount"] * 0.5,
                               money_key=f"transfer:{p['transfer_id']}"))
        elif st in ("FAILED", "REJECTED"):
            out.append(Finding("payouts", "FAILED_PAYOUT", p["transfer_id"], p["amount"], "medium",
                               f"{inr(p['amount'])} to {p['payee']} {st} ({code}). Invoice still unpaid.",
                               dict(payee=p["payee"], status_code=code, invoice=p["invoice"]),
                               action="FIX_BENEFICIARY_DETAILS" if "ACCOUNT" in str(code) else "RETRY_LATER", priority=p["amount"] * 0.5,
                               money_key=f"transfer:{p['transfer_id']}"))


def _decide_requests(L, reg, out):
    paid_ok = [p for p in reg if p["live"].get("status") == "SUCCESS"]
    pending = [p for p in reg if p["live"].get("status") == "PENDING"]
    for q in L["payout_requests"]:
        ev = dict(payee=q["payee"], amount=q["amount"], invoice=q.get("invoice"))
        screen = sanctions_screen(q["payee"]) if SANCTIONS_SCREENING else dict(hit=False, skipped="SANCTIONS_SCREENING off")
        ev["sanctions"] = screen
        ifsc_ok = ifsc_valid(q["ifsc"]) if q.get("ifsc") else True; ev["ifsc"] = q.get("ifsc")
        dup = next((p for p in paid_ok if (p["payee"].upper() == q["payee"].upper() and p["invoice"] == q.get("invoice"))
                    or (q.get("beneficiary_id") and p["beneficiary_id"] == q["beneficiary_id"] and paise(p["amount"]) == paise(q["amount"]))), None)
        ev["duplicate_of"] = dup["transfer_id"] if dup else None
        prior = next((p for p in pending if name_match(p["payee"], q["payee"])[0] != "NO_MATCH"), None)
        ev["prior_pending"] = prior["transfer_id"] if prior else None
        if q.get("beneficiary_id") and any(p["beneficiary_id"] == q["beneficiary_id"] for p in paid_ok):
            nm = "MATCH"; ev["name_check"] = "known beneficiary with successful payout history"
        elif q.get("vpa"):
            v = cfio.upi_name_at_bank(q["vpa"], q["payee"]); at_bank = v.get("name_at_bank")
            nm = name_match(q["payee"], at_bank)[0] if at_bank else "NO_MATCH"
            ev["name_check"] = dict(name_at_bank=at_bank, result=nm, vpa=q["vpa"], status=v.get("status"))
        else:
            nm = "NO_MATCH"; ev["name_check"] = "new payee, bank account not verified"
        d = decide("payout_policy.json", dict(sanctions_hit=screen["hit"], ifsc_valid=ifsc_ok, duplicate=bool(dup),
                                               prior_pending=bool(prior), name_result=nm, amount=q["amount"]))
        sev = {"BLOCK": "high", "REVIEW": "medium", "APPROVE": "info"}[d["decision"]]
        out.append(Finding("payouts", "PAYOUT_DECISION", q["request_id"], q["amount"], sev,
                           f"{q['request_id']} {q['payee']} {inr(q['amount'])}: {d['decision']}: {d['reason']}.",
                           ev, action=d["decision"], needs_approval=d["decision"] == "APPROVE", priority=q["amount"]))


def run_payouts(ctx):
    L, out = ctx["ledger"], []
    reg = [dict(p, live=cfio.transfer(p["transfer_id"])) for p in L["payouts"]]
    _audit(reg, out)
    _decide_requests(L, reg, out)
    return out

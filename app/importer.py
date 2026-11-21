"""Batch import: CSV or JSON from the operator. Tolerates new headers, reports every invalid row,
and never stores protected attributes or their common proxies."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime

from .context import Ctx
from .db import IST, iso, new_id, now
from .money import AmountError, format_inr, parse_rupees_to_paise
from .signals import extract
from .specialists import allocate_receipts, handoff, record_claim

ALIASES = {
    "account_ref": ["account_id", "account_ref", "loan_id", "loan_no", "loan_number", "loan_account", "customer_id",
                    "buyer_id", "account", "account_no", "borrower_id", "party_code", "party_id", "case_id"],
    "due_ref": ["invoice_id", "invoice_no", "invoice_number", "invoice", "instalment_id", "installment_id", "emi_id", "due_id", "bill_no"],
    "amount": ["amount_due", "amount", "outstanding", "balance", "due_amount", "emi_amount", "invoice_amount",
               "overdue_amount", "amount_inr", "outstanding_amount", "total_due"],
    "due_date": ["due_date", "date", "invoice_date", "emi_date", "due_on"],
    "payment_ref": ["payment_ref", "payment_reference", "utr", "order_id", "cf_order_id", "reference", "txn_ref", "receipt_ref"],
    "notes": ["notes", "note", "field_notes", "remarks", "comments", "call_notes", "agent_notes"],
    "language": ["language", "preferred_language", "lang", "language_pref"],
    "phone": ["phone", "mobile", "contact", "phone_number", "mobile_no", "contact_number"],
    "email": ["email", "email_id"],
    "name": ["name", "borrower_name", "buyer_name", "customer_name", "party_name", "account_name"],
    "days_past_due": ["dpd", "days_past_due", "days_overdue", "overdue_days"],
    "on_time_payments_6m": ["on_time_payments_6m", "on_time_6m", "paid_on_time_6m", "ontime_6m"],
    "missed_payments_6m": ["missed_payments_6m", "missed_6m", "bounces_6m"],
    "broken_promises": ["broken_promises", "ptp_broken", "promises_broken"],
    "approved_credit": ["approved_credit", "approved_credit_note", "credit_note_amount"],
    "credit_ref": ["credit_note_ref", "credit_note_id"],
}
# Never stored and never scored: protected characteristics and common proxies for them.
PROTECTED = {"gender", "sex", "religion", "caste", "community", "category", "sub_caste", "pincode", "pin", "pin_code",
             "postal_code", "area", "locality", "district", "village", "city", "region", "marital_status", "age", "dob",
             "date_of_birth", "father_name", "spouse_name", "surname", "mother_tongue", "nationality", "disability"}
# Outcome columns a judge may include for scoring; held out from the model entirely.
HELD_OUT = {"outcome", "paid_within_7d", "true_outcome", "label", "paid_next_7d"}
MAX_ROWS = 20000

LANG = {"ta": "ta-IN", "tamil": "ta-IN", "தமிழ்": "ta-IN", "ta-in": "ta-IN", "hi": "hi-IN", "hindi": "hi-IN",
        "हिंदी": "hi-IN", "hi-in": "hi-IN", "en": "en-IN", "english": "en-IN", "en-in": "en-IN",
        "hinglish": "hi-IN", "tanglish": "ta-IN"}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def _map_headers(headers: list[str]) -> tuple[dict, list[str], list[str], list[str]]:
    mapping, protected, held, unknown = {}, [], [], []
    for h in headers:
        n = _norm_header(h)
        if n in PROTECTED:
            protected.append(h)
            continue
        if n in HELD_OUT:
            held.append(h)
            continue
        for field, names in ALIASES.items():
            if n in names and field not in mapping:
                mapping[field] = h
                break
        else:
            unknown.append(h)
    return mapping, protected, held, unknown


def _parse_date(s: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unreadable date {s!r}")


def _int_or_none(s):
    s = str(s or "").strip()
    if not s:
        return None
    if not re.fullmatch(r"\d{1,5}", s):
        raise ValueError(f"not a whole number: {s!r}")
    return int(s)


def read_rows(filename: str, content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    if filename.lower().endswith(".json") or text.lstrip().startswith(("[", "{")):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("rows") or data.get("accounts") or data.get("records") or []
        if not isinstance(data, list):
            raise ValueError("JSON must be a list of records")
        return [{str(k): ("" if v is None else str(v)) for k, v in r.items()} for r in data if isinstance(r, dict)]
    return list(csv.DictReader(io.StringIO(text)))


def import_batch(ctx: Ctx, merchant_id: str, filename: str, content: bytes, actor: str) -> dict:
    db = ctx.db
    try:
        rows = read_rows(filename, content)
    except (ValueError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Could not read file: {e}"}
    if len(rows) > MAX_ROWS:
        return {"ok": False, "error": f"Too many rows ({len(rows)} > {MAX_ROWS})"}
    headers = list(rows[0].keys()) if rows else []
    mapping, protected, held, unknown = _map_headers(headers)
    missing = [f for f in ("account_ref", "amount") if f not in mapping]
    if missing:
        return {"ok": False, "error": f"Missing required column(s): {', '.join(missing)}",
                "headers_seen": headers, "accepted_names": {f: ALIASES[f] for f in missing}}
    mode = db.one("SELECT mode FROM merchants WHERE id=?", (merchant_id,))["mode"]
    today = now().astimezone(IST).date()
    batch_id = new_id("batch")
    invalid, warnings, amount_changes = [], [], []
    seen_due: set[str] = set()
    touched: dict[str, dict] = {}
    get = lambda r, f: (r.get(mapping[f]) or "").strip() if f in mapping else ""

    valid_rows = []
    for i, r in enumerate(rows, start=2):  # row 1 is the header
        errs = []
        acct_ref = get(r, "account_ref")
        if not acct_ref:
            errs.append("account/loan/customer id is empty")
        elif not re.fullmatch(r"[A-Za-z0-9_\-/.]{1,64}", acct_ref):
            errs.append(f"account id has unexpected characters: {acct_ref!r}")
        try:
            amount = parse_rupees_to_paise(get(r, "amount"))
            if amount == 0:
                errs.append("amount due is zero")
        except AmountError as e:
            amount = None
            errs.append(str(e))
        try:
            due_date = _parse_date(get(r, "due_date"))
        except ValueError as e:
            due_date, _ = None, errs.append(str(e))
        feats = {}
        for f in ("days_past_due", "on_time_payments_6m", "missed_payments_6m", "broken_promises"):
            try:
                v = _int_or_none(get(r, f))
                if v is not None:
                    feats[f] = v
            except ValueError as e:
                errs.append(f"{f}: {e}")
        if "days_past_due" not in feats and due_date:
            feats["days_past_due"] = max((today - date.fromisoformat(due_date)).days, 0)
        due_ref = get(r, "due_ref") or (f"{acct_ref}-{due_date or 'DUE'}" if acct_ref else "")
        if due_ref and due_ref in seen_due:
            errs.append(f"duplicate invoice/instalment id {due_ref!r} in this file")
        phone = re.sub(r"\D", "", get(r, "phone"))[-10:]
        if get(r, "phone") and len(phone) != 10:
            errs.append("phone is not a 10-digit number")
        credit = None
        if get(r, "approved_credit"):
            try:
                credit = parse_rupees_to_paise(get(r, "approved_credit"))
            except AmountError as e:
                errs.append(f"approved credit: {e}")
        if errs:
            invalid.append({"row": i, "account_ref": acct_ref or None, "errors": errs})
            continue
        seen_due.add(due_ref)
        lang_raw = get(r, "language").lower()
        lang = LANG.get(lang_raw, "en-IN" if not lang_raw else None)
        if lang is None:
            lang = "en-IN"
            warnings.append({"row": i, "warning": f"unknown language {get(r, 'language')!r}; using en-IN for messages"})
        valid_rows.append(dict(row=i, acct_ref=acct_ref, due_ref=due_ref, amount=amount, due_date=due_date, feats=feats,
                               phone=phone or None, email=get(r, "email") or None, name=get(r, "name") or None,
                               lang=lang, notes=get(r, "notes"), payment_ref=get(r, "payment_ref") or None,
                               credit=credit, credit_ref=get(r, "credit_ref") or None))

    with db.tx():
        for v in valid_rows:
            acct = db.one("SELECT * FROM accounts WHERE merchant_id=? AND external_ref=?", (merchant_id, v["acct_ref"]))
            if not acct:
                aid = new_id("acct")
                db.x("INSERT INTO accounts (id, merchant_id, external_ref, display_name, phone, email, language, features_json,"
                     " batch_id, ledger_read_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (aid, merchant_id, v["acct_ref"], v["name"], v["phone"], v["email"], v["lang"], json.dumps(v["feats"]),
                      batch_id, iso(), iso()))
            else:
                aid = acct["id"]
                feats = json.loads(acct["features_json"] or "{}") if acct["batch_id"] == batch_id else {}
                feats.update(v["feats"])
                db.x("UPDATE accounts SET display_name=COALESCE(?, display_name), phone=COALESCE(?, phone),"
                     " email=COALESCE(?, email), language=?, features_json=?, batch_id=?, ledger_read_at=? WHERE id=?",
                     (v["name"], v["phone"], v["email"], v["lang"], json.dumps(feats), batch_id, iso(), aid))
            touched.setdefault(aid, {"ref": v["acct_ref"], "rows": []})["rows"].append(v)
            due = db.one("SELECT * FROM dues WHERE merchant_id=? AND external_ref=?", (merchant_id, v["due_ref"]))
            if due and due["account_id"] != aid:
                invalid.append({"row": v["row"], "account_ref": v["acct_ref"],
                                "errors": [f"invoice {v['due_ref']} already belongs to another account; not moved"]})
                continue
            if due:
                if due["amount_paise"] != v["amount"]:
                    amount_changes.append({"invoice": v["due_ref"], "from": format_inr(due["amount_paise"]),
                                           "to": format_inr(v["amount"])})
                db.x("UPDATE dues SET amount_paise=?, due_date=?, payment_ref=COALESCE(?, payment_ref), batch_id=?, superseded=0"
                     " WHERE id=?", (v["amount"], v["due_date"], v["payment_ref"], batch_id, due["id"]))
            else:
                db.x("INSERT INTO dues (id, merchant_id, account_id, external_ref, amount_paise, due_date, payment_ref, batch_id)"
                     " VALUES (?,?,?,?,?,?,?,?)", (new_id("due"), merchant_id, aid, v["due_ref"], v["amount"], v["due_date"],
                                                    v["payment_ref"], batch_id))
            if v["credit"]:
                ref = v["credit_ref"] or f"credit-{v['due_ref']}"
                if not db.one("SELECT 1 FROM credits WHERE merchant_id=? AND reference=?", (merchant_id, ref)):
                    db.x("INSERT INTO credits (id, merchant_id, account_id, amount_paise, reference, status, source, created_at)"
                         " VALUES (?,?,?,?,?,?,?,?)", (new_id("cr"), merchant_id, aid, v["credit"], ref, "approved",
                                                       f"ledger import {batch_id}", iso()))
            db.bump_version(aid)

    # Claims from untrusted text, then evidence checks, outside the import transaction.
    claims_made = 0
    for aid, t in touched.items():
        for v in t["rows"]:
            if v["payment_ref"] and not db.one("SELECT 1 FROM claims WHERE account_id=? AND kind='paid' AND reference=?",
                                               (aid, v["payment_ref"])):
                record_claim(ctx, merchant_id, aid, "paid", f"import:payment_ref:{batch_id}",
                             text=f"ledger row {v['due_ref']} lists payment reference", reference=v["payment_ref"])
                claims_made += 1
            if v["notes"]:
                claims_made += _claims_from_note(ctx, merchant_id, aid, v["notes"], f"import:note:{batch_id}", mode, today)
        handoff(ctx, merchant_id, aid, "evidence", "refresh_account", actor=actor)
        allocate_receipts(ctx, merchant_id, aid)

    report = {"ok": True, "batch_id": batch_id, "filename": filename, "total_rows": len(rows),
              "valid_rows": len(valid_rows), "invalid_rows": len(invalid), "invalid": invalid, "warnings": warnings,
              "accounts_touched": len(touched), "claims_from_notes_and_refs": claims_made,
              "amount_changes": amount_changes, "column_mapping": mapping,
              "protected_columns_dropped": protected, "held_out_columns": held, "unrecognized_columns": unknown}
    db.x("INSERT INTO batches (id, merchant_id, filename, imported_at, total_rows, valid_rows, invalid_rows, report_json)"
         " VALUES (?,?,?,?,?,?,?,?)", (batch_id, merchant_id, filename, iso(), len(rows), len(valid_rows), len(invalid),
                                       json.dumps(report, ensure_ascii=False)))
    db.trace(merchant_id, "import", actor, target="importer", result={k: report[k] for k in (
        "batch_id", "total_rows", "valid_rows", "invalid_rows", "protected_columns_dropped", "held_out_columns")})
    return report


def _claims_from_note(ctx: Ctx, merchant_id: str, aid: str, note: str, source: str, mode: str, today) -> int:
    s = extract(note, today)
    n = 0
    db = ctx.db
    if db.one("SELECT 1 FROM claims WHERE account_id=? AND text=? AND source LIKE 'import:note:%'", (aid, note[:500])):
        return 0  # same note re-imported
    if s.opt_out:
        record_claim(ctx, merchant_id, aid, "opt_out", source, text=note); n += 1
    if s.paid_claim:
        record_claim(ctx, merchant_id, aid, "paid", source, text=note, amount_paise=s.paid_amount_paise, reference=s.reference); n += 1
    if s.deduction:
        record_claim(ctx, merchant_id, aid, "deduction", source, text=note, amount_paise=s.deduction_amount_paise); n += 1
    if s.hardship:
        record_claim(ctx, merchant_id, aid, "hardship", source, text=note); n += 1
    if s.salary_day:
        record_claim(ctx, merchant_id, aid, "salary_date", source, text=str(s.salary_day)); n += 1
    if s.promise and s.promised_date:
        record_claim(ctx, merchant_id, aid, "promise", source, text=note, amount_paise=s.amount_paise,
                     promised_date=s.promised_date); n += 1
    return n

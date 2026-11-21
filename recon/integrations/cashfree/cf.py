#!/usr/bin/env python3
"""
Cashfree sandbox playground: a stdlib-only client + CLI. Every function is a ready-made agent tool.

Setup:  cp .env.example .env   (fill in sandbox keys)
Usage:  python3 cf.py smoke                 # run end-to-end checks for whichever keys are present
        python3 cf.py link 450 9999999999 "Groceries"
        python3 cf.py order 100             # create order + pay it headlessly via test UPI
        python3 cf.py get-order <order_id>
        python3 cf.py refund <order_id> 50
        python3 cf.py pan ABCPV1234D "John Doe" 1993-06-30
        python3 cf.py gstin 29AAICP2912R1ZR
        python3 cf.py bank 026291800001191 YESB0000262
        python3 cf.py namematch "Ramesh Kumar" "RAMESH KR"
        python3 cf.py payout 10 success@upi       # creates beneficiary + transfer
        python3 cf.py balance                     # payouts balance (v1)
        python3 cf.py recharge <paymentInstrumentId> 100000   # sandbox notional top-up
        python3 cf.py raw GET /pg/orders/<id>     # call anything (family inferred from path)
"""
import base64
import json
import re
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_env(path=os.path.join(HERE, ".env")):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
ENV = os.getenv("CF_ENV", "sandbox")
HOST = "https://sandbox.cashfree.com" if ENV == "sandbox" else "https://api.cashfree.com"

FAMILIES = {
    "pg": dict(id="CF_PG_CLIENT_ID", secret="CF_PG_CLIENT_SECRET",
               version=os.getenv("CF_PG_API_VERSION", "2025-01-01")),
    "payout": dict(id="CF_PAYOUT_CLIENT_ID", secret="CF_PAYOUT_CLIENT_SECRET", version="2024-01-01"),
    # Secure ID shares keys with Payouts unless you set separate ones.
    "verification": dict(id="CF_VERIFY_CLIENT_ID", secret="CF_VERIFY_CLIENT_SECRET", version="2024-12-01"),
}


class CashfreeError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status, self.body = status, body


def _creds(family):
    f = FAMILIES[family]
    cid, sec = os.getenv(f["id"]), os.getenv(f["secret"])
    if family == "verification" and not cid:
        cid, sec = os.getenv("CF_PAYOUT_CLIENT_ID"), os.getenv("CF_PAYOUT_CLIENT_SECRET")
    return cid, sec


def has_keys(family):
    return all(_creds(family))


def _signature(client_id, family="payout"):
    """x-cf-signature for Payouts/Secure ID when your IP isn't whitelisted. Needs `pip install cryptography`.
    Secure ID may live on a different account than Payouts, so it can use its own key (CF_VERIFY_PUBLIC_KEY_PATH)."""
    pem = (family == "verification" and os.getenv("CF_VERIFY_PUBLIC_KEY_PATH")) or os.getenv("CF_PUBLIC_KEY_PATH")
    if not pem:
        return None
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_public_key(open(os.path.expanduser(pem), "rb").read())
    msg = f"{client_id}.{int(time.time())}".encode()
    enc = key.encrypt(msg, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
    return base64.b64encode(enc).decode()


def call(method, path, body=None, family=None, idempotent=True):
    """Low-level request. path starts with /pg, /payout or /verification."""
    family = family or path.strip("/").split("/")[0]
    cid, sec = _creds(family)
    if not (cid and sec):
        raise RuntimeError(f"Missing keys for '{family}' in .env")
    headers = {
        "x-client-id": cid, "x-client-secret": sec,
        "x-api-version": FAMILIES[family]["version"],
        "Content-Type": "application/json", "Accept": "application/json",
        "x-request-id": str(uuid.uuid4()),
    }
    if method == "POST" and idempotent and family == "pg":
        headers["x-idempotency-key"] = str(uuid.uuid4())
    if family in ("payout", "verification"):
        sig = _signature(cid, family)
        if sig:
            headers["x-cf-signature"] = sig
    data = json.dumps(body).encode() if body is not None else None
    # Network-level retry (TLS EOF, resets, timeouts). Safe for POSTs because every create call carries its
    # own unique id (order_id / transfer_id / refund_id / link_id), which Cashfree de-duplicates.
    for attempt in range(4):
        req = urllib.request.Request(HOST + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
            raise CashfreeError(e.code, raw) from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            if attempt == 3:
                raise CashfreeError(0, f"network error after retries: {e}") from None
            time.sleep(1.5 * (attempt + 1))
            if family in ("payout", "verification"):
                sig = _signature(cid, family)
                if sig:
                    headers["x-cf-signature"] = sig
    raise CashfreeError(0, "unreachable")



def _id(prefix):
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}"


# ─────────────────────────── PAYMENTS (money in) ───────────────────────────
def create_payment_link(amount, phone, purpose, name=None, email=None, notify_sms=False, expiry_minutes=None, link_id=None, notify_url=None):
    body = {
        "link_id": link_id or _id("link"), "link_amount": float(amount), "link_currency": "INR",
        "link_purpose": purpose,
        "customer_details": {k: v for k, v in
                             {"customer_phone": phone, "customer_name": name, "customer_email": email}.items() if v},
        "link_notify": {"send_sms": notify_sms, "send_email": bool(email) and notify_sms},
    }
    if expiry_minutes:
        exp = time.strftime("%Y-%m-%dT%H:%M:%S+05:30", time.gmtime(time.time() + 19800 + expiry_minutes * 60))
        body["link_expiry_time"] = exp
    if notify_url:  # Cashfree POSTs PAYMENT_LINK_EVENT / payment webhooks here
        body["link_meta"] = {"notify_url": notify_url}
    return call("POST", "/pg/links", body)


def get_payment_link(link_id):
    return call("GET", f"/pg/links/{link_id}")


def cancel_payment_link(link_id):
    return call("POST", f"/pg/links/{link_id}/cancel", {})


def get_link_orders(link_id):
    return call("GET", f"/pg/links/{link_id}/orders")


def create_order(amount, phone="9999999999", customer_id=None, note=None, return_url=None, notify_url=None):
    body = {
        "order_id": _id("order"), "order_amount": float(amount), "order_currency": "INR",
        "customer_details": {"customer_id": customer_id or f"cust_{phone}", "customer_phone": phone},
    }
    meta = {k: v for k, v in {"return_url": return_url, "notify_url": notify_url}.items() if v}
    if meta:
        body["order_meta"] = meta
    if note:
        body["order_note"] = note
    return call("POST", "/pg/orders", body)


def pay_order_upi(payment_session_id, upi_id="testsuccess@gocash"):
    """Headless Order Pay: sends a UPI collect request. Sandbox VPAs: testsuccess@gocash / testfailure@gocash."""
    return call("POST", "/pg/orders/sessions", {
        "payment_session_id": payment_session_id,
        "payment_method": {"upi": {"channel": "collect", "upi_id": upi_id}},
    }, idempotent=False)


def get_order(order_id):
    return call("GET", f"/pg/orders/{order_id}")


def get_order_payments(order_id):
    return call("GET", f"/pg/orders/{order_id}/payments")


def refund(order_id, amount, note="Refund"):
    return call("POST", f"/pg/orders/{order_id}/refunds",
                {"refund_amount": float(amount), "refund_id": _id("rf"), "refund_note": note})


def get_refunds(order_id):
    return call("GET", f"/pg/orders/{order_id}/refunds")


def get_order_settlements(order_id):
    return call("GET", f"/pg/orders/{order_id}/settlements")


def simulate_payment(cf_payment_id, status="SUCCESS", error_code=""):
    """Sandbox only: force a payment outcome (SUCCESS / FAILED / PENDING...)."""
    return call("POST", "/pg/simulate", {
        "entity": "PAYMENTS", "entity_id": str(cf_payment_id),
        "entity_simulation": {"payment_status": status, "payment_error_code": error_code},
    })


# ─────────────────────────── PAYOUTS (money out) ───────────────────────────
def create_beneficiary(name, vpa=None, bank_account=None, ifsc=None, phone=None, beneficiary_id=None):
    inst = {}
    if vpa:
        inst["vpa"] = vpa
    if bank_account:
        inst.update(bank_account_number=bank_account, bank_ifsc=ifsc)
    body = {"beneficiary_id": beneficiary_id or _id("bene"), "beneficiary_name": name,
            "beneficiary_instrument_details": inst}
    if phone:
        body["beneficiary_contact_details"] = {"beneficiary_phone": phone}
    return call("POST", "/payout/beneficiary", body)


def get_beneficiary(beneficiary_id):
    return call("GET", f"/payout/beneficiary?beneficiary_id={beneficiary_id}")


def transfer(amount, beneficiary_id, mode=None, remarks="Payout", transfer_id=None):
    # Cashfree rejects '-', ':' etc. in remarks (transfer_remarks_invalid): keep letters, digits, spaces; max 70
    remarks = re.sub(r"[^A-Za-z0-9 ]", " ", remarks or "Payout")
    remarks = re.sub(r" +", " ", remarks).strip()[:70] or "Payout"
    body = {"transfer_id": transfer_id or _id("tr"), "transfer_amount": float(amount),
            "beneficiary_details": {"beneficiary_id": beneficiary_id}, "transfer_remarks": remarks}
    if mode:  # banktransfer / imps / neft / rtgs / upi
        body["transfer_mode"] = mode
    return call("POST", "/payout/transfers", body)


def get_transfer(transfer_id):
    return call("GET", f"/payout/transfers?transfer_id={transfer_id}")


# Payouts v1 bearer-token APIs (balance, fund sources, notional recharge). Recharge needs a fund
# source paymentInstrumentId that Cashfree provisions; until then it returns "paymentInstrumentId not available".
def _payout_v1(method, path, body=None):
    cid, sec = _creds("payout")
    auth = {"X-Client-Id": cid, "X-Client-Secret": sec}
    sig = _signature(cid)
    if sig:
        auth["X-Cf-Signature"] = sig
    def go(m, p, b, h):
        data = json.dumps(b).encode() if b is not None else (b"" if m == "POST" else None)
        req = urllib.request.Request(HOST + "/payout" + p, data=data, method=m, headers={"Content-Type": "application/json", **h})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    tok = go("POST", "/v1/authorize", None, auth)
    if tok.get("status") != "SUCCESS":
        return tok
    return go(method, path, body, {"Authorization": "Bearer " + tok["data"]["token"]})


def get_balance():
    return _payout_v1("GET", "/v1/getBalance")


def get_fund_source(payment_instrument_id):
    return _payout_v1("GET", f"/v1/fundsources/{payment_instrument_id}")


def recharge_fund_source(payment_instrument_id, amount, remarks="sandbox test balance"):
    """Notional credit to a fund source (sandbox test money). rechargeId must be alphanumeric."""
    return _payout_v1("POST", "/v1/connected-wallet/recharge",
                      {"amount": float(amount), "paymentInstrumentId": payment_instrument_id,
                       "rechargeId": f"rc{int(time.time())}{uuid.uuid4().hex[:6]}", "remarks": remarks})


# ─────────────────────────── SECURE ID (verification) ───────────────────────────
def verify_bank(account, ifsc, name=None):
    body = {"bank_account": account, "ifsc": ifsc}
    if name:
        body["name"] = name
    return call("POST", "/verification/bank-account/sync", body)


def verify_pan(pan, name, dob):
    return call("POST", "/verification/pan-lite",
                {"verification_id": _id("pan"), "pan": pan, "name": name, "dob": dob})


def verify_gstin(gstin, business_name=None):
    body = {"GSTIN": gstin}
    if business_name:
        body["business_name"] = business_name
    return call("POST", "/verification/gstin", body)


def name_match(name_1, name_2):
    return call("POST", "/verification/name-match",
                {"verification_id": _id("nm"), "name_1": name_1, "name_2": name_2})


# ─────────────────────────── smoke test ───────────────────────────
def _step(label, fn):
    try:
        out = fn()
        print(f"  ✅ {label}")
        return out
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {label}: {e}")
        return None


def smoke():
    print(f"Cashfree {ENV} smoke test")
    if has_keys("pg"):
        print("\n[Payments]")
        link = _step("create payment link", lambda: create_payment_link(1, "9999999999", "Smoke test"))
        if link:
            print("     →", link.get("link_url"), link.get("link_status"))
        order = _step("create order ₹10", lambda: create_order(10))
        if order:
            oid, sid = order["order_id"], order["payment_session_id"]
            pay = _step("pay order via UPI testsuccess@gocash", lambda: pay_order_upi(sid))
            if pay:
                print("     →", json.dumps(pay)[:200])
            time.sleep(3)
            o = _step("get order status", lambda: get_order(oid))
            if o:
                print("     → order_status =", o.get("order_status"))
            p = _step("list payments", lambda: get_order_payments(oid))
            if p:
                print("     →", [(x.get("cf_payment_id"), x.get("payment_status")) for x in p])
            if o and o.get("order_status") == "PAID":
                r = _step("refund ₹5", lambda: refund(oid, 5))
                if r:
                    print("     → refund_status =", r.get("refund_status"))
    else:
        print("\n[Payments] skipped: no CF_PG_* keys")

    if has_keys("verification"):
        print("\n[Secure ID]")
        for label, fn in [
            ("PAN lite (valid)", lambda: verify_pan("ABCPV1234D", "John Doe", "1993-06-30")),
            ("GSTIN", lambda: verify_gstin("29AAICP2912R1ZR")),
            ("bank account", lambda: verify_bank("026291800001191", "YESB0000262")),
            ("name match", lambda: name_match("Ramesh Kumar", "RAMESH KR")),
        ]:
            r = _step(label, fn)
            if r:
                print("     →", json.dumps(r)[:200])
    else:
        print("\n[Secure ID] skipped: no CF_VERIFY_* / CF_PAYOUT_* keys")

    if has_keys("payout"):
        print("\n[Payouts]")
        b = _step("create UPI beneficiary success@upi", lambda: create_beneficiary("Test Worker", vpa="success@upi"))
        if b:
            t = _step("transfer ₹1", lambda: transfer(1, b["beneficiary_id"], mode="upi"))
            if t:
                print("     → status =", t.get("status"), t.get("transfer_id"))
    else:
        print("\n[Payouts] skipped: no CF_PAYOUT_* keys")


# ─────────────────────────── CLI ───────────────────────────
def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, a = argv[0], argv[1:]
    cmds = {
        "smoke": lambda: smoke(),
        "link": lambda: create_payment_link(a[0], a[1], a[2] if len(a) > 2 else "Payment"),
        "get-link": lambda: get_payment_link(a[0]),
        "order": lambda: (lambda o: {"order": o, "pay": pay_order_upi(o["payment_session_id"],
                                                                      a[1] if len(a) > 1 else "testsuccess@gocash")})(
            create_order(a[0])),
        "get-order": lambda: {"order": get_order(a[0]), "payments": get_order_payments(a[0])},
        "refund": lambda: refund(a[0], a[1]),
        "settlements": lambda: get_order_settlements(a[0]),
        "simulate": lambda: simulate_payment(a[0], a[1] if len(a) > 1 else "SUCCESS"),
        "pan": lambda: verify_pan(a[0], a[1], a[2]),
        "gstin": lambda: verify_gstin(a[0]),
        "bank": lambda: verify_bank(a[0], a[1], a[2] if len(a) > 2 else None),
        "namematch": lambda: name_match(a[0], a[1]),
        "payout": lambda: (lambda b: {"beneficiary": b, "transfer": transfer(a[0], b["beneficiary_id"])})(
            create_beneficiary("Test Payee", vpa=a[1])),
        "get-transfer": lambda: get_transfer(a[0]),
        "balance": lambda: get_balance(),
        "fundsource": lambda: get_fund_source(a[0]),
        "recharge": lambda: recharge_fund_source(a[0], a[1]),
        "raw": lambda: call(a[0].upper(), a[1], json.loads(a[2]) if len(a) > 2 else None),
    }
    if cmd not in cmds:
        print(f"Unknown command {cmd}\n{__doc__}")
        sys.exit(1)
    try:
        out = cmds[cmd]()
        if out is not None:
            print(json.dumps(out, indent=2, ensure_ascii=False))
    except CashfreeError as e:
        print(json.dumps({"http_status": e.status, "error": e.body}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])

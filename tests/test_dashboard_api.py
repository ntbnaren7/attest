"""Endpoints the dashboard relies on: localized reasons, money totals, spoken explanations."""
from conftest import row, upload


def test_queue_totals_and_localized_reasons(meena):
    upload(meena, "borrower_batch_A.csv")
    q = meena.get("/api/queue", params={"lang": "ta-IN"}).json()
    assert q["totals"]["received_paise"] == 950000   # L-1002 ₹6,500 + L-1008 partial ₹3,000 (fixtures)
    assert q["totals"]["outstanding_paise"] == sum(r["outstanding_paise"] for r in q["rows"])
    r = next(x for x in q["rows"] if x["external_ref"] == "L-1005")
    assert "சிரமம்" in r["reason_local"]                                 # hardship, in Tamil
    en = meena.get("/api/queue").json()
    assert all(x["reason_local"] == x["reason"] for x in en["rows"])


def test_explain_is_scoped_and_localized(meena, arun):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1006")["account_id"]
    ex = meena.get(f"/api/cases/{acct}/explain", params={"lang": "hi-IN"}).json()
    assert "कॉल न करने" in ex["text"] and ex["audio_b64"] is None       # no Sarvam key in tests
    assert arun.get(f"/api/cases/{acct}/explain").status_code == 404


def test_signature_dot_form_is_accepted_and_reported():
    from datetime import datetime, timezone
    import base64, hashlib, hmac
    from app.providers.cashfree import verify_webhook_signature
    raw, ts, key = b'{"a":1}', str(int(datetime.now(timezone.utc).timestamp() * 1000)), "k"
    dot = base64.b64encode(hmac.new(key.encode(), ts.encode() + b"." + raw, hashlib.sha256).digest()).decode()
    ok, why = verify_webhook_signature(raw, ts, dot, key, 300)
    assert ok and "dot" in why


def test_kannada_reasons_help_and_assistant(meena):
    upload(meena, "borrower_batch_A.csv")
    q = meena.get("/api/queue", params={"lang": "kn-IN"}).json()
    r = next(x for x in q["rows"] if x["external_ref"] == "L-1006")
    assert "ಕರೆ ಮಾಡಬೇಡಿ" in r["reason_local"]                     # opt-out, in Kannada
    h = meena.get("/api/help/send_link", params={"lang": "kn-IN"}).json()
    assert "ಪಾವತಿ ಲಿಂಕ್" in h["text"]
    turn = meena.post("/api/coordinator/text", json={"text": "L-1012 ಖಾತೆಯ ಬಾಕಿ ಎಷ್ಟು?"}).json()
    assert turn["reply_language"] == "kn-IN" and "ಬಾಕಿ" in turn["reply"]
    assert turn["account"]["external_ref"] == "L-1012"


def test_claim_source_file_is_exposed(meena):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1004")["account_id"]
    c = meena.get(f"/api/cases/{acct}").json()
    promise = next(cl for cl in c["claims"] if cl["kind"] == "promise")
    batch = promise["source"].split(":")[2]
    assert c["batch_files"][batch] == "borrower_batch_A.csv"
    assert promise["text"] == "salary 5th ko aayega tab de dunga"   # original words kept

"""Customer messages through the vendored voice layer (stubbed here; live runs use Sarvam)."""
import pytest

from conftest import row, upload

from app import inbox


class FakeLayer:
    def __init__(self, reply, lang="hi-IN", entities=None):
        self.reply, self.lang, self.entities = reply, lang, entities or {}

    def process_whatsapp_text(self, text, sender_phone=None, language_hint=None):
        return {"success": True, "transcript": text, "detected_language": self.lang, "reply_text": self.reply,
                "reply_audio_base64": None, "cashfree_action": {"target_action": "payment_recovery", "entities": self.entities}}


@pytest.fixture
def fake(monkeypatch):
    def use(reply, **kw):
        monkeypatch.setattr(inbox, "_layer", lambda ctx: FakeLayer(reply, **kw))
    return use


def test_promise_message_becomes_a_claim_and_holds_the_account(meena, fake):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1012")["account_id"]
    fake("धन्यवाद, हमने आपकी बात नोट कर ली है।")
    out = meena.post("/api/inbox/message", data={"phone": "+919999999990", "text": "kal 3300 de dunga", "account_id": acct}).json()
    assert [c["kind"] for c in out["claims"]] == ["promise"]
    assert out["decision"]["code"] == "PROMISE_ACTIVE" and out["reply_source"] == "template"   # offline: no grounded writer


def test_paid_claim_triggers_a_cashfree_check(meena, fake):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1003")["account_id"]
    fake("Thanks, noted.", lang="en-IN")
    out = meena.post("/api/inbox/message", data={"text": "I already paid, ref CFORD-L1002", "account_id": acct}).json()
    assert ("evidence", "check_payment_reference") in {(h["to"], h["action"]) for h in out["handoffs"]}
    assert out["decision"]["code"] == "EVIDENCE_CONFLICT"     # that payment belongs to another account


def test_reply_that_promises_money_is_replaced(meena, fake):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1003")["account_id"]
    fake("Your refund of ₹25,000 has been processed right away!", lang="en-IN")
    out = meena.post("/api/inbox/message", data={"text": "branch mein de diya", "account_id": acct}).json()
    assert out["reply_source"] == "template" and "refund" not in out["reply"].lower()
    assert "will check your payment" in out["reply"]


def test_unverified_amount_in_reply_is_rejected():
    ok, why = inbox.guard_reply("You owe ₹99,999.", allowed_paise={420000})
    assert not ok and "amount" in why
    assert inbox.guard_reply("You owe ₹4,200.", allowed_paise={420000})[0]


def test_sender_must_map_to_one_account_in_this_book(meena, arun, fake):
    upload(meena, "borrower_batch_A.csv")
    fake("ok")
    out = meena.post("/api/inbox/message", data={"phone": "+919999999999", "text": "hello"}).json()
    assert out["reply_source"] == "needs_account" and len(out["candidates"]) > 1   # shared test numbers
    other = row(meena, "L-1001")["account_id"]
    out = arun.post("/api/inbox/message", data={"text": "hello", "account_id": other}).json()
    assert out["account"] is None                                                  # another tenant's account


def test_thread_keeps_messages_and_facts_are_verified_only(meena, fake):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1012")["account_id"]
    fake("Noted, thank you.", lang="en-IN")
    meena.post("/api/inbox/message", data={"text": "how much do I owe?", "account_id": acct})
    meena.post("/api/inbox/message", data={"text": "I will pay tomorrow", "account_id": acct})
    t = meena.get(f"/api/inbox/thread/{acct}").json()
    assert [m["text"] for m in t["messages"]] == ["how much do I owe?", "I will pay tomorrow"]
    assert all(m["reply"] for m in t["messages"]) and t["messages"][1]["claims"] == ["promise"]
    assert t["facts"]["amount_due"] == "₹3,300.50" and "_allowed_paise" not in t["facts"]


def test_thread_is_tenant_scoped(meena, arun):
    upload(meena, "borrower_batch_A.csv")
    assert arun.get(f"/api/inbox/thread/{row(meena, 'L-1012')['account_id']}").status_code == 404


def test_waiver_or_refund_request_gets_a_clear_human_review_answer(meena, fake):
    upload(meena, "borrower_batch_A.csv")
    fake("ok", lang="en-IN")
    out = meena.post("/api/inbox/message", data={"text": "please waive the fee and refund 5000",
                                                 "account_id": row(meena, "L-1011")["account_id"]}).json()
    assert "can't approve a waiver or refund" in out["reply"] and "₹12,000" in out["reply"]


def test_photo_of_receipt_becomes_a_paid_claim_and_is_checked(meena, fake, monkeypatch):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1003")["account_id"]
    fake("ok", lang="en-IN")
    monkeypatch.setattr(inbox, "read_image", lambda ctx, data, name: {
        "extracted_text": "Payment Successful\nRs 5,100 to Lender\nRef CFORD-L1002", "detected_languages": ["English"],
        "review_required": False, "errors": []})
    out = meena.post("/api/inbox/message", data={"account_id": acct},
                     files={"image": ("receipt.jpg", b"\xff\xd8fakejpeg", "image/jpeg")}).json()
    assert out["ocr"]["text"].startswith("Payment Successful")
    assert [c["kind"] for c in out["claims"]] == ["paid"]
    assert ("evidence", "check_payment_reference") in {(h["to"], h["action"]) for h in out["handoffs"]}
    thread = meena.get(f"/api/inbox/thread/{acct}").json()["messages"]
    assert thread[-1]["via"] == "image"


def test_non_image_upload_is_refused(meena, fake):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1003")["account_id"]
    r = meena.post("/api/inbox/message", data={"account_id": acct}, files={"image": ("x.exe", b"MZ", "application/x-msdownload")})
    assert r.status_code == 415


def test_receipt_amount_is_the_rupee_figure_not_the_utr(meena, fake, monkeypatch):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1003")["account_id"]
    fake("ok", lang="en-IN", entities={"transaction_id": "533891274405"})   # the live voice layer extracts it
    monkeypatch.setattr(inbox, "read_image", lambda ctx, d, n: {
        "extracted_text": "Payment Successful\n₹5,000\nUPI Transaction ID\n533891274405", "errors": []})
    out = meena.post("/api/inbox/message", data={"account_id": acct},
                     files={"image": ("s.png", b"\x89PNG", "image/png")}).json()
    assert "5,33" not in row(meena, "L-1003")["reason"] and "₹5,000" in row(meena, "L-1003")["reason"]
    assert "do not need to send it again" in out["reply"]

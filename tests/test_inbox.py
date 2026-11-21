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

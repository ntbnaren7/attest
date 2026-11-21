"""Adding customers (by hand / from a ledger page) and business -> customer messages."""
from conftest import row, upload

from app import customers


def test_propose_from_ledger_lines():
    assert customers.propose("Ravi Kumar - 5000 baaki, 9876543210") == {
        "name": "Ravi Kumar", "amount_paise": 500000, "phone": "9876543210", "note": "Ravi Kumar - 5000 baaki, 9876543210"}
    assert customers.propose("Collection diary - 21 Nov 2026") is None
    assert customers.propose("Suresh: ₹12,500 due by 30th")["amount_paise"] == 1250000


def test_add_customer_goes_through_the_importer(meena):
    upload(meena, "borrower_batch_A.csv")
    rep = meena.post("/api/customers", json={"customers": [
        {"name": "Ravi Kumar", "amount": "5000", "phone": "9999999999", "language": "hi"},
        {"name": "No Amount", "amount": ""}]}).json()
    assert rep["valid_rows"] == 1 and rep["invalid_rows"] == 1
    r = row(meena, rep["refs"][0])
    assert r["display_name"] == "Ravi Kumar" and r["outstanding_paise"] == 500000


def test_business_reminder_and_message(meena):
    upload(meena, "borrower_batch_A.csv")
    acct = row(meena, "L-1012")["account_id"]
    d = meena.get(f"/api/inbox/draft/{acct}").json()
    assert d["ok"] and "₹3,300.50" in d["text"] and "L-1012" in d["text"]
    assert meena.post("/api/inbox/send", json={"account_id": acct, "text": d["text"]}).json()["ok"]
    thread = meena.get(f"/api/inbox/thread/{acct}").json()["messages"]
    assert thread[-1]["from"] == "business" and thread[-1]["reply"] == d["text"]
    # an invented amount or a refund promise is not sent
    assert meena.post("/api/inbox/send", json={"account_id": acct, "text": "We waived ₹99,999 and refunded you"}).status_code == 409


def test_no_reminder_for_held_or_do_not_contact(meena):
    upload(meena, "borrower_batch_A.csv")
    held = row(meena, "L-1004")["account_id"]          # promise on record
    assert not meena.get(f"/api/inbox/draft/{held}").json()["ok"]
    dnc = row(meena, "L-1006")["account_id"]           # asked not to be called
    assert meena.post("/api/inbox/send", json={"account_id": dnc, "text": "Hello"}).status_code == 409

"""A photographed ledger page: OCR (stubbed), lines matched to accounts, claims recorded, queue re-forms."""
from conftest import row, upload

from app import inbox, ledger

PAGE = """Collection diary 21 Nov
Kavitha Raman - salary 5th ko aayega tab de dungi
L-1002 Rohan: says paid 6500 at branch, ref CFORD-L1002
Selvi - husband in hospital, abhi nahi de sakti
Unknown Person - will call back"""


def test_line_matching_by_id_full_name_and_unique_first_name():
    rows = [{"id": "a1", "external_ref": "L-1001", "display_name": "Kavitha Raman"},
            {"id": "a2", "external_ref": "L-1002", "display_name": "Rohan Mehta"},
            {"id": "a3", "external_ref": "L-1005", "display_name": "Selvi Murugan"},
            {"id": "a4", "external_ref": "L-1006", "display_name": "Selvi Kumar"}]
    assert ledger.match_line(rows, "l1002 paid")["id"] == "a2"
    assert ledger.match_line(rows, "Kavitha Raman paid")["id"] == "a1"
    assert ledger.match_line(rows, "Kavitha paid")["id"] == "a1"
    assert ledger.match_line(rows, "Selvi paid") is None          # two Selvis: never guess


def test_scan_records_each_line_on_the_right_account(meena, monkeypatch):
    upload(meena, "borrower_batch_A.csv")
    monkeypatch.setattr(inbox, "read_image", lambda ctx, d, n: {"extracted_text": PAGE, "review_required": False, "errors": []})
    out = meena.post("/api/ledger/scan", files={"image": ("page.jpg", b"\xff\xd8x", "image/jpeg")}).json()
    by_ref = {r["account"]["external_ref"]: r for r in out["lines"] if r["account"]}
    assert set(by_ref) == {"L-1001", "L-1002", "L-1005"} and out["matched"] == 3
    assert by_ref["L-1001"]["code"] == "PROMISE_ACTIVE"
    assert by_ref["L-1005"]["code"] == "HARDSHIP"
    assert any(not r["account"] and "Unknown" in r["line"] for r in out["lines"])
    assert row(meena, "L-1005")["decision"] == "REVIEW"

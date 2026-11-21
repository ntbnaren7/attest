"""The 24/7 recovery daemon prepares work for approval; it never creates or sends a link itself."""
from conftest import upload


def test_daemon_prepares_next_customers_for_on_screen_approval(meena, ctx):
    upload(meena, "borrower_batch_A.csv")
    out = meena.post("/api/daemon/run").json()
    assert out["eligible"] > 0 and 0 < len(out["prepared"]) <= 3
    rows = ctx.db.all("SELECT status, requested_via FROM approvals WHERE requested_via='daemon'")
    assert len(rows) == len(out["prepared"]) and all(r["status"] == "pending" for r in rows)
    assert not ctx.db.all("SELECT 1 FROM payment_links")            # nothing created without a person approving
    again = meena.post("/api/daemon/run").json()
    assert not set(again["prepared"]) & set(out["prepared"])         # does not re-prepare the same customers
    status = meena.get("/api/daemon").json()
    assert status["last"]["at"] and "running" in status


def test_daemon_is_scoped_to_the_operator_merchant(meena, arun, ctx):
    upload(meena, "borrower_batch_A.csv")
    assert arun.post("/api/daemon/run").json()["eligible"] == 0

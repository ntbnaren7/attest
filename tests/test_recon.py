"""The vendored reconciliation engine, driven through our API on recorded Cashfree data."""
from fastapi.testclient import TestClient


def test_recon_runs_on_recorded_data_and_ties_out(meena):
    st = meena.get("/api/recon/status").json()
    assert st["available"] and st["mode"] == "recorded"
    v = meena.post("/api/recon/run").json()
    kinds = {f["kind"] for f in v["findings"]}
    assert v["kpi"]["findings"] == 12 and abs(v["kpi"]["at_risk"] - 82513.18) < 0.01
    assert {"CAPTURED_NOT_SETTLED", "FULFILLED_BUT_UNPAID", "DUPLICATE_REFUND", "MISSING_CREDIT"} <= kinds
    assert v["pending"], "expected an approval batch"


def test_recon_approval_is_dry_run_once_and_scoped(meena, arun):
    v = meena.post("/api/recon/run").json()
    tid, ids = v["thread_id"], [p["id"] for p in v["pending"]]
    # another tenant cannot decide this batch
    assert arun.post("/api/recon/approve", json={"thread_id": tid, "decisions": {i: True for i in ids}}).status_code == 404
    out = meena.post("/api/recon/approve", json={"thread_id": tid, "decisions": {i: True for i in ids}}).json()
    assert out["results"] and all(r["status"] in ("DRY_RUN", "ADVISORY") for r in out["results"])
    assert meena.post("/api/recon/approve", json={"thread_id": tid, "decisions": {}}).status_code == 409


def test_recon_needs_sign_in(app):
    assert TestClient(app).post("/api/recon/run").status_code == 401

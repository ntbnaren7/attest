"""Abuse and cost protections."""
import pytest
from fastapi.testclient import TestClient

from conftest import login, row, upload

from app import security
from app.security import Limit


@pytest.fixture
def tight(monkeypatch):
    """Tighten limits to small, deterministic values for one test (burst with no refill)."""
    def set_(kind, burst):
        monkeypatch.setitem(security.LIMITS, kind, Limit(per_minute=0.0001, burst=burst))
    return set_


def test_assistant_is_rate_limited_per_operator(app, tight):
    tight("assistant", 3)
    m = login(app, "meena")
    codes = [m.post("/api/coordinator/text", json={"text": "L-1001 status"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    r = m.post("/api/coordinator/text", json={"text": "L-1001 status"})
    assert int(r.headers["retry-after"]) >= 1
    # Another operator has their own allowance.
    assert login(app, "arun").post("/api/coordinator/text", json={"text": "B-501 status"}).status_code == 200


def test_long_utterance_is_refused_before_any_model_call(meena):
    r = meena.post("/api/coordinator/text", json={"text": "x" * 600})
    assert r.status_code == 413


def test_login_lockout_after_repeated_failures(app):
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/login", json={"username": "meena", "password": "wrong"}).status_code == 401
    # Even the right password is refused while locked.
    r = c.post("/api/login", json={"username": "meena", "password": "demo-pass-123"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_money_actions_are_rate_limited(app, tight):
    tight("money", 2)
    m = login(app, "meena")
    upload(m, "borrower_batch_A.csv")
    acct = row(m, "L-1001")["account_id"]
    codes = [m.post(f"/api/cases/{acct}/request-link").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_daily_model_budget(app, monkeypatch):
    sec = app.state.security
    monkeypatch.setattr(sec.budget, "limit", 3)
    sec.budget.charge("lender_demo", 3)
    with pytest.raises(Exception) as e:
        sec.budget.charge("lender_demo", 1)
    assert getattr(e.value, "status_code", None) == 429
    sec.budget.charge("supplier_demo", 1)     # budgets are per tenant


def test_inflight_guard_blocks_parallel_requests(app):
    sec = app.state.security
    sec.inflight.acquire("op1")
    with pytest.raises(Exception) as e:
        sec.inflight.acquire("op1")
    assert getattr(e.value, "status_code", None) == 429
    sec.inflight.release("op1")
    sec.inflight.acquire("op1")


def test_security_headers_and_hidden_docs(app):
    c = TestClient(app)
    r = c.get("/classic")
    for h in ("content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy", "permissions-policy"):
        assert h in r.headers, h
    assert "microphone=(self)" in r.headers["permissions-policy"]
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    assert c.get("/api/me").headers.get("cache-control") == "no-store"


def test_cross_site_post_is_blocked(meena):
    r = meena.post("/api/coordinator/text", json={"text": "hi"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "BAD_ORIGIN"
    ok = meena.post("/api/coordinator/text", json={"text": "L-1001 status"}, headers={"Origin": "http://localhost:8000"})
    assert ok.status_code == 200


def test_oversized_bodies_rejected_before_reading(app):
    c = TestClient(app)
    big = b"{" + b" " * 70_000 + b"}"
    assert c.post("/webhooks/cashfree", content=big, headers={"content-type": "application/json"}).status_code == 413


def test_webhooks_rate_limited_per_ip(app, tight):
    tight("webhook", 3)
    c = TestClient(app)
    codes = [c.post("/webhooks/cashfree", json={"type": "x"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_fixture_checkout_page_escapes_input(app):
    r = TestClient(app).get("/sim/pay/%3Cscript%3Ealert(1)")
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text


def test_help_speaks_only_fixed_topics(meena):
    r = meena.get("/api/help/send_link", params={"lang": "ta-IN"})
    assert r.status_code == 200 and "கட்டண இணைப்பை" in r.json()["text"]
    assert meena.get("/api/help/../../etc/passwd").status_code == 404
    assert meena.get("/api/help/anything-else").status_code == 404


def test_demo_session_only_locally_and_only_when_enabled(app, monkeypatch):
    c = TestClient(app)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert c.post("/api/demo/session", json={"persona": "meena"}).status_code == 403
    monkeypatch.setenv("DEMO_MODE", "1")
    # Through a proxy/tunnel (forwarded headers) it is refused even when enabled.
    r = c.post("/api/demo/session", json={"persona": "meena"}, headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403
    assert c.get("/api/demo", headers={"X-Forwarded-For": "203.0.113.9"}).json()["enabled"] is False
    # Locally it signs in as the chosen scenario's operator, and unknown personas are rejected.
    assert c.post("/api/demo/session", json={"persona": "root"}).status_code == 400
    assert c.post("/api/demo/session", json={"persona": "arun"}).status_code == 200
    assert c.get("/api/me").json()["operator"]["username"] == "arun"

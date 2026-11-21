import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RECEIVABLES_NO_AUTOAPP"] = "1"
# Tests never use live credentials, even if a developer's .env has them.
for k in ("CASHFREE_CLIENT_ID", "CASHFREE_CLIENT_SECRET", "SARVAM_API_KEY", "SARVAM_VOICE_API_KEY", "RELAY_WEBHOOK_URL",
          "RECON_CF_PG_CLIENT_SECRET", "RECON_CF_PAYOUT_CLIENT_SECRET"):
    os.environ[k] = ""
# Workflow tests make many quick calls; security tests tighten these limits explicitly.
for k in ("RL_LOGIN_PER_MIN", "RL_ASSISTANT_PER_MIN", "RL_SPEECH_PER_MIN", "RL_PROVIDER_PER_MIN", "RL_MONEY_PER_MIN",
          "RL_IMPORT_PER_MIN", "RL_WEBHOOK_PER_MIN", "RL_READ_PER_MIN"):
    os.environ[k] = "100000"

from fastapi.testclient import TestClient  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.config import Settings  # noqa: E402
from app.context import Ctx  # noqa: E402
from app.main import create_app  # noqa: E402

FIX = ROOT / "fixtures"
NOON_IST = datetime.fromisoformat("2026-11-21T11:00:00+05:30")


@pytest.fixture
def ctx(tmp_path):
    dbmod.set_now(NOON_IST)
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    c = Ctx(s)
    yield c
    dbmod.set_now(None)


@pytest.fixture
def app(ctx):
    return create_app(ctx, start_reconciler=False)


def login(app, username="meena"):
    c = TestClient(app)
    r = c.post("/api/login", json={"username": username, "password": "demo-pass-123"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def meena(app):
    return login(app, "meena")


@pytest.fixture
def arun(app):
    return login(app, "arun")


def upload(client, name):
    p = FIX / name
    r = client.post("/api/import", files={"file": (name, p.read_bytes(), "text/csv")})
    return r


def row(client, ref, batch_id=None):
    q = client.get("/api/queue", params={"batch_id": batch_id} if batch_id else {}).json()
    return next(r for r in q["rows"] if r["external_ref"] == ref)


SCREEN = {"x-approval-screen": "1"}


def approve_and_execute(client, approval):
    confirm = {k: approval[k] for k in ("account_id", "action", "amount_paise", "account_version", "recipient")}
    r = client.post(f"/api/approvals/{approval['id']}/approve", json={"confirm": confirm}, headers=SCREEN)
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    return client.post(f"/api/approvals/{approval['id']}/execute", json={"token": tok}, headers=SCREEN)

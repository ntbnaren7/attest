"""SQLite persistence. One file, WAL mode, explicit schema. All amounts are INTEGER paise."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

_now_override: datetime | None = None


def now() -> datetime:
    """Server clock (timezone aware). Tests can pin it with set_now()."""
    return _now_override or datetime.now(timezone.utc)


def set_now(dt: datetime | None) -> None:
    global _now_override
    _now_override = dt


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS merchants (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('borrower','merchant'))
);
CREATE TABLE IF NOT EXISTS operators (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL REFERENCES merchants(id),
  username TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL, display_name TEXT NOT NULL,
  can_approve INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES operators(id),
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, filename TEXT, imported_at TEXT NOT NULL,
  total_rows INTEGER, valid_rows INTEGER, invalid_rows INTEGER, report_json TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, external_ref TEXT NOT NULL,
  display_name TEXT, phone TEXT, email TEXT, language TEXT,
  do_not_contact INTEGER NOT NULL DEFAULT 0,
  features_json TEXT NOT NULL DEFAULT '{}',   -- allow-listed scoring features only
  batch_id TEXT, version INTEGER NOT NULL DEFAULT 1,
  ledger_read_at TEXT, provider_checked_at TEXT, provider_check_error TEXT, evidence_conflict TEXT, updated_at TEXT,
  UNIQUE (merchant_id, external_ref)
);
CREATE TABLE IF NOT EXISTS dues (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, account_id TEXT NOT NULL,
  external_ref TEXT NOT NULL, amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
  due_date TEXT, payment_ref TEXT, batch_id TEXT, superseded INTEGER NOT NULL DEFAULT 0,
  UNIQUE (merchant_id, external_ref)
);
CREATE TABLE IF NOT EXISTS credits (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, account_id TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK (amount_paise > 0), reference TEXT,
  status TEXT NOT NULL CHECK (status IN ('approved')), source TEXT, created_at TEXT
);
-- Claims are statements by a payer, a field note or the operator. They never change a balance.
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, account_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('paid','promise','deduction','hardship','opt_out','dispute','salary_date')),
  amount_paise INTEGER, reference TEXT, promised_date TEXT, text TEXT,
  source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  status_reason TEXT, created_at TEXT NOT NULL, checked_at TEXT
);
-- Receipts exist only when a provider reported a successful payment.
CREATE TABLE IF NOT EXISTS receipts (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, account_id TEXT,
  provider TEXT NOT NULL, provider_payment_id TEXT NOT NULL, order_id TEXT, link_id TEXT,
  amount_paise INTEGER NOT NULL, payment_status TEXT NOT NULL, payment_time TEXT,
  verified_at TEXT NOT NULL, provenance TEXT NOT NULL,
  UNIQUE (merchant_id, provider, provider_payment_id)
);
CREATE TABLE IF NOT EXISTS allocations (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, receipt_id TEXT NOT NULL, due_id TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK (amount_paise > 0), basis TEXT NOT NULL,
  ledger_status TEXT NOT NULL DEFAULT 'proposed', created_at TEXT NOT NULL,
  UNIQUE (receipt_id, due_id)
);
CREATE TABLE IF NOT EXISTS payment_links (
  link_id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, account_id TEXT NOT NULL,
  approval_id TEXT NOT NULL UNIQUE, amount_paise INTEGER NOT NULL,
  amount_paid_paise INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, link_url TEXT,
  provenance TEXT NOT NULL, created_at TEXT NOT NULL, last_checked_at TEXT, raw_json TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, account_id TEXT NOT NULL,
  action TEXT NOT NULL, amount_paise INTEGER NOT NULL, account_version INTEGER NOT NULL,
  recipient TEXT NOT NULL, requested_by TEXT NOT NULL, requested_via TEXT NOT NULL,
  approved_by TEXT, approved_via TEXT, token_hash TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','used','rejected','stale','expired')),
  status_reason TEXT, created_at TEXT NOT NULL, approved_at TEXT, expires_at TEXT, used_at TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, account_id TEXT NOT NULL,
  action TEXT NOT NULL, idem_key TEXT NOT NULL UNIQUE, channel TEXT NOT NULL,
  status TEXT NOT NULL, request_json TEXT, response_json TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
  id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE, merchant_id TEXT,
  signature_ok INTEGER NOT NULL, event_type TEXT, link_id TEXT, order_id TEXT,
  received_at TEXT NOT NULL, outcome TEXT, raw TEXT
);
CREATE TABLE IF NOT EXISTS trace (
  id INTEGER PRIMARY KEY AUTOINCREMENT, merchant_id TEXT NOT NULL, account_id TEXT,
  conversation_id TEXT, kind TEXT NOT NULL, actor TEXT NOT NULL, target TEXT,
  allowed_action TEXT, input_json TEXT, result_json TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queue_snapshot (
  account_id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, decision TEXT, rank INTEGER,
  collectible_paise INTEGER, reason TEXT, computed_at TEXT
);
-- Fixture-only provider state. Never read when a tenant runs against the live sandbox.
CREATE TABLE IF NOT EXISTS sim_links (
  link_id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, body_json TEXT NOT NULL,
  amount_paise INTEGER NOT NULL, amount_paid_paise INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, created_at TEXT NOT NULL, idem_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS sim_payments (
  cf_payment_id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, order_id TEXT NOT NULL,
  link_id TEXT, amount_paise INTEGER NOT NULL, payment_status TEXT NOT NULL, payment_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trace_acct ON trace(merchant_id, account_id, id);
CREATE INDEX IF NOT EXISTS ix_claims_acct ON claims(account_id);
CREATE INDEX IF NOT EXISTS ix_dues_acct ON dues(account_id);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def tx(self):
        """Serialized write transaction (re-entrant)."""
        with self.lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
                self._depth -= 1
                if outer:
                    self.conn.execute("COMMIT")
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise

    def x(self, sql: str, params=()):
        with self.lock:
            return self.conn.execute(sql, params)

    def one(self, sql: str, params=()):
        with self.lock:
            r = self.conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    def all(self, sql: str, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def trace(self, merchant_id: str, kind: str, actor: str, *, account_id=None, target=None,
              allowed_action=None, input=None, result=None, conversation_id=None) -> int:
        cur = self.x(
            "INSERT INTO trace (merchant_id, account_id, conversation_id, kind, actor, target, allowed_action,"
            " input_json, result_json, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (merchant_id, account_id, conversation_id, kind, actor, target, allowed_action,
             json.dumps(input, ensure_ascii=False, default=str) if input is not None else None,
             json.dumps(result, ensure_ascii=False, default=str) if result is not None else None, iso()))
        return cur.lastrowid

    def bump_version(self, account_id: str) -> int:
        self.x("UPDATE accounts SET version = version + 1, updated_at=? WHERE id=?", (iso(), account_id))
        return self.one("SELECT version FROM accounts WHERE id=?", (account_id,))["version"]

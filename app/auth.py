"""Operator authentication. Every lookup downstream is scoped by the session's merchant_id."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from .db import DB, iso, new_id, now, parse_iso

SESSION_HOURS = 12


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(dk.hex(), dk_hex)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@dataclass(frozen=True)
class Operator:
    id: str
    merchant_id: str
    username: str
    display_name: str
    can_approve: bool
    mode: str
    merchant_name: str


def create_operator(db: DB, merchant_id: str, username: str, password: str, display_name: str,
                    can_approve: bool = True) -> str:
    oid = new_id("op")
    db.x("INSERT INTO operators (id, merchant_id, username, pw_hash, display_name, can_approve) VALUES (?,?,?,?,?,?)",
         (oid, merchant_id, username, hash_password(password), display_name, int(can_approve)))
    return oid


def login(db: DB, username: str, password: str) -> str | None:
    row = db.one("SELECT * FROM operators WHERE username=?", (username,))
    if not row or not verify_password(password, row["pw_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    db.x("INSERT INTO sessions (token_hash, operator_id, created_at, expires_at) VALUES (?,?,?,?)",
         (sha256(token), row["id"], iso(), iso(now() + timedelta(hours=SESSION_HOURS))))
    return token


def session_for(db: DB, username: str) -> str | None:
    """Issue a session for a known operator without a password. Only callable from the local demo route."""
    row = db.one("SELECT id FROM operators WHERE username=?", (username,))
    if not row:
        return None
    token = secrets.token_urlsafe(32)
    db.x("INSERT INTO sessions (token_hash, operator_id, created_at, expires_at) VALUES (?,?,?,?)",
         (sha256(token), row["id"], iso(), iso(now() + timedelta(hours=SESSION_HOURS))))
    return token


def logout(db: DB, token: str) -> None:
    db.x("DELETE FROM sessions WHERE token_hash=?", (sha256(token),))


def operator_for_token(db: DB, token: str | None) -> Operator | None:
    if not token:
        return None
    row = db.one(
        "SELECT o.*, s.expires_at, m.mode, m.name AS merchant_name FROM sessions s "
        "JOIN operators o ON o.id = s.operator_id JOIN merchants m ON m.id = o.merchant_id "
        "WHERE s.token_hash=?", (sha256(token),))
    if not row or parse_iso(row["expires_at"]) < now():
        return None
    return Operator(row["id"], row["merchant_id"], row["username"], row["display_name"],
                    bool(row["can_approve"]), row["mode"], row["merchant_name"])

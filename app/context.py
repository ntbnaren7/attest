"""Wiring: one DB, per-tenant payment provider, Sarvam client, Relay adapter."""
from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT, Settings
from .db import DB
from .providers.cashfree import CashfreeFixture, CashfreeLive
from .providers.relay import RelayAdapter
from .providers.sarvam import SarvamLive


class Ctx:
    def __init__(self, settings: Settings, db: DB | None = None):
        self.settings = settings
        self.db = db or DB(settings.db_path)
        static = {}
        fx = ROOT / "fixtures" / "cashfree_fixture_orders.json"
        if fx.exists():
            static = json.loads(fx.read_text())
        self._static_orders = static
        self._fixtures: dict[str, CashfreeFixture] = {}
        self._live = None
        if settings.cashfree_live:
            self._live = CashfreeLive(settings.cashfree_base_url, settings.cashfree_client_id,
                                      settings.cashfree_client_secret, settings.cashfree_api_version)
        self.sarvam = SarvamLive(settings.sarvam_api_key, settings.sarvam_base_url, settings.sarvam_chat_model,
                                 settings.sarvam_stt_model, settings.sarvam_tts_model,
                                 settings.sarvam_tts_speaker, fallback_keys=(settings.sarvam_voice_api_key,)) if settings.sarvam_live else None
        self.relay = RelayAdapter(settings.relay_webhook_url, settings.relay_webhook_secret)

    def cashfree_for(self, merchant_id: str):
        """Live sandbox only for the configured tenant; every other tenant gets its own labeled fixture."""
        if self._live and merchant_id == self.settings.cashfree_live_merchant:
            return self._live
        if merchant_id not in self._fixtures:
            self._fixtures[merchant_id] = CashfreeFixture(self.db, merchant_id, self._static_orders.get(merchant_id, {}))
        return self._fixtures[merchant_id]

    def integration_status(self, merchant_id: str) -> dict:
        cf = self.cashfree_for(merchant_id)
        return {
            "cashfree": {"mode": cf.provenance, "label": "sandbox (live API)" if cf.provenance == "cashfree_sandbox"
                         else "FIXTURE - simulated responses", "api_version": self.settings.cashfree_api_version},
            "sarvam": {"mode": "live" if self.sarvam else "unavailable",
                       "label": f"live ({self.settings.sarvam_chat_model}, {self.settings.sarvam_stt_model}, "
                                f"{self.settings.sarvam_tts_model})" if self.sarvam
                       else "NO KEY - rule-based extraction, no speech"},
            "relay": {"mode": self.relay.mode, "label": "inbound webhook" if self.relay.mode == "inbound_webhook"
                      else "MANUAL step - no trigger URL configured"},
        }

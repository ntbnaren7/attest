"""Live provider smoke tests. Uses credentials from the environment / .env only; prints no secrets.

  .venv/bin/python scripts/smoke_live.py

Records outcome per integration as: verified in provider test | blocked (reason) to evidence/smoke_<ts>.json
"""
from __future__ import annotations

import base64
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.coordinator import SYSTEM, TOOL  # noqa: E402
from app.providers.cashfree import CashfreeLive, ProviderError  # noqa: E402
from app.providers.relay import RelayAdapter  # noqa: E402
from app.providers.sarvam import SarvamError, SarvamLive, parse_tool_call  # noqa: E402

results = {}


def cashfree():
    if not settings.cashfree_live:
        return {"status": "blocked", "reason": "CASHFREE_CLIENT_ID / CASHFREE_CLIENT_SECRET not set"}
    cf = CashfreeLive(settings.cashfree_base_url, settings.cashfree_client_id, settings.cashfree_client_secret,
                      settings.cashfree_api_version)
    out = {"base_url": settings.cashfree_base_url, "api_version": settings.cashfree_api_version}
    link_id = f"smoke_{uuid.uuid4().hex[:10]}"
    try:
        st = cf.create_link(link_id=link_id, amount_paise=100, customer_phone="9999999999", customer_name="Smoke Test",
                            customer_email=None, purpose="Smoke test (synthetic)", expiry=datetime.now(timezone.utc) + timedelta(days=1),
                            notes={"synthetic": "true"}, notify_url=None, return_url=None, idem_key=str(uuid.uuid4()),
                            allow_partial=False)
        out["create_link"] = {"link_id": st.link_id, "status": st.status, "amount_paise": st.amount_paise, "link_url": st.link_url}
        st2 = cf.get_link(link_id)
        out["get_link"] = {"status": st2.status, "amount_paid_paise": st2.amount_paid_paise}
        out["get_link_orders"] = cf.get_link_orders(link_id)
        try:
            cf.get_order_payments("order_that_does_not_exist_" + uuid.uuid4().hex[:6])
            out["unknown_order"] = "unexpected success"
        except ProviderError as e:
            out["unknown_order"] = f"{e.kind} (expected not_found)"
        out["status"] = "verified in provider test"
    except ProviderError as e:
        out.update(status="blocked", reason=f"{e.kind}: {e}")
    return out


def sarvam():
    if not settings.sarvam_live:
        return {"status": "blocked", "reason": "SARVAM_API_KEY not set"}
    s = SarvamLive(settings.sarvam_api_key, settings.sarvam_base_url, settings.sarvam_chat_model, settings.sarvam_stt_model,
                   settings.sarvam_tts_model, settings.sarvam_tts_speaker)
    out = {}
    try:
        text = "L-1001 के लिए पचास हज़ार नहीं, पाँच हज़ार रुपये का लिंक भेजिए"
        r = s.chat([{"role": "system", "content": SYSTEM.format(today=datetime.now().date().isoformat())},
                    {"role": "user", "content": text}], tools=[TOOL],
                   tool_choice={"type": "function", "function": {"name": "route_operator_request"}})
        name, args = parse_tool_call(r["message"])
        out["chat_tool_call"] = {"model": r["model"], "tool": name, "arguments": args}
        tts = s.tts("L-1001 पर चार हज़ार दो सौ रुपये बकाया है।", "hi-IN")
        audio = base64.b64decode(tts["audio_b64"] or "")
        out["tts"] = {"model": tts["model"], "bytes": len(audio)}
        (ROOT / "evidence").mkdir(exist_ok=True)
        (ROOT / "evidence" / "smoke_tts_hi.wav").write_bytes(audio)
        stt = s.stt(audio, "smoke.wav", "audio/wav", "unknown")
        out["stt_roundtrip"] = {"transcript": stt["transcript"], "language_code": stt["language_code"],
                                "note": "ASR on our own TTS proves the API path only, not recognition quality"}
        out["status"] = "verified in provider test" if name == "route_operator_request" else "partial: no tool call returned"
    except SarvamError as e:
        out.update(status="blocked", reason=str(e)[:300])
    return out


def relay():
    r = RelayAdapter(settings.relay_webhook_url, settings.relay_webhook_secret)
    packet = {"case_id": "smoke", "allowed_action": "operator_alert_test", "test_mode": True,
              "message": "Smoke test from receivables queue (synthetic)"}
    res = r.deliver(packet, f"smoke-{uuid.uuid4().hex[:8]}")
    if res["mode"] == "manual":
        return {"status": "blocked", "reason": "RELAY_WEBHOOK_URL not set; Relay documents no public run API. Manual Test-flow step required.",
                "packet": packet}
    return {"status": "verified in provider test" if res["status"] == "accepted" else "blocked", **res,
            "note": "Accepted by the Relay inbound webhook; confirm the run in Relay > Runs."}


if __name__ == "__main__":
    for name, fn in (("cashfree", cashfree), ("sarvam", sarvam), ("relay", relay)):
        try:
            results[name] = fn()
        except Exception as e:  # report, never crash the smoke run
            results[name] = {"status": "blocked", "reason": f"{e.__class__.__name__}: {e}"[:300]}
        print(f"{name}: {results[name]['status']}" + (f" ({results[name].get('reason')})" if results[name].get("reason") else ""))
    out = ROOT / "evidence" / f"smoke_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print(f"evidence: {out.relative_to(ROOT)}")

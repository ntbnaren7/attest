"""Customer-side messages (WhatsApp-style voice notes or text) through the teammate's voice layer (voice/).

Flow: his layer transcribes (Saaras), extracts entities and drafts a natural reply (sarvam-105b-conversations)
-> we match the sender to an account in the operator's book -> our rule-based signal reader turns the words
into claims (paid / promise / deduction / hardship / do-not-call) -> a "paid" claim with a reference triggers
a Cashfree check -> the queue decision updates.

The reply is his natural reply only if it passes a guard: no amount the verified facts or the customer's own
message do not contain, and no promise of money movement. Otherwise a fixed reply in the customer's language
is sent. Nothing here can move money or approve anything.
"""
from __future__ import annotations

import re
import sys
import threading
from datetime import timedelta

from .config import ROOT
from .context import Ctx
from .db import IST, now
from .indic_numbers import corrected_amount
from .money import format_inr
from .signals import extract
from .specialists import evaluate, handoff

VOICE_DIR = ROOT / "voice"
_lock = threading.Lock()
_layer_cache: dict = {}

# Phrases a customer-facing reply must never contain: they promise money movement or a completed check.
PROMISES = re.compile(
    r"refund(ed)?|process(ed|ing)? (the|your)|payout|transferr?ed|credited|reversed|waive|secur(e|ing) (your )?(funds|money)"
    r"|right away|immediately|has been (paid|cleared|settled)|वापस (कर|मिल)|रिफंड|ரீஃபண்ட்|திருப்பி அனுப்ப|ಮರುಪಾವತಿ",
    re.IGNORECASE)

REPLIES = {
    "en-IN": {
        "paid": "Thank you. We will check your payment with Cashfree and will not send reminders while we check.",
        "promise": "Thank you, we have noted that you will pay{amt} by {date}.",
        "deduction": "Thank you. A person from our team will review the deduction you mentioned before anything else.",
        "hardship": "We are sorry to hear that. Someone from our team will contact you about options. No reminders until then.",
        "opt_out": "Understood. We will not call this number again.",
        "other": "Thank you for your message. Your current amount due is {due}.",
    },
    "hi-IN": {
        "paid": "धन्यवाद। हम Cashfree से आपका भुगतान जाँचेंगे और जाँच तक कोई रिमाइंडर नहीं भेजेंगे।",
        "promise": "धन्यवाद, हमने नोट कर लिया है कि आप {date} तक{amt} भुगतान करेंगे।",
        "deduction": "धन्यवाद। आपकी बताई कटौती को हमारी टीम का एक व्यक्ति पहले देखेगा।",
        "hardship": "यह सुनकर दुख हुआ। हमारी टीम का कोई व्यक्ति विकल्पों के लिए आपसे संपर्क करेगा। तब तक कोई रिमाइंडर नहीं।",
        "opt_out": "ठीक है। हम इस नंबर पर दोबारा कॉल नहीं करेंगे।",
        "other": "आपके संदेश के लिए धन्यवाद। आपकी अभी की बकाया राशि {due} है।",
    },
    "ta-IN": {
        "paid": "நன்றி. உங்கள் கட்டணத்தை Cashfree-இல் சரிபார்ப்போம்; சரிபார்க்கும் வரை நினைவூட்டல் அனுப்ப மாட்டோம்.",
        "promise": "நன்றி, நீங்கள் {date} க்குள்{amt} செலுத்துவீர்கள் என்று பதிவு செய்துள்ளோம்.",
        "deduction": "நன்றி. நீங்கள் குறிப்பிட்ட கழிவை எங்கள் குழுவினர் ஒருவர் முதலில் பார்ப்பார்.",
        "hardship": "இதைக் கேட்டு வருந்துகிறோம். மாற்று வழிகளுக்காக எங்கள் குழுவினர் உங்களைத் தொடர்பு கொள்வார். அதுவரை நினைவூட்டல் இல்லை.",
        "opt_out": "சரி. இந்த எண்ணுக்கு இனி அழைக்க மாட்டோம்.",
        "other": "உங்கள் செய்திக்கு நன்றி. உங்கள் தற்போதைய நிலுவை {due}.",
    },
    "kn-IN": {
        "paid": "ಧನ್ಯವಾದ. ನಿಮ್ಮ ಪಾವತಿಯನ್ನು Cashfree ನಲ್ಲಿ ಪರಿಶೀಲಿಸುತ್ತೇವೆ; ಪರಿಶೀಲನೆ ಮುಗಿಯುವವರೆಗೆ ನೆನಪೋಲೆ ಕಳುಹಿಸುವುದಿಲ್ಲ.",
        "promise": "ಧನ್ಯವಾದ, ನೀವು {date} ರೊಳಗೆ{amt} ಪಾವತಿಸುತ್ತೀರಿ ಎಂದು ದಾಖಲಿಸಿದ್ದೇವೆ.",
        "deduction": "ಧನ್ಯವಾದ. ನೀವು ಹೇಳಿದ ಕಡಿತವನ್ನು ನಮ್ಮ ತಂಡದ ಒಬ್ಬರು ಮೊದಲು ಪರಿಶೀಲಿಸುತ್ತಾರೆ.",
        "hardship": "ಇದನ್ನು ಕೇಳಿ ಬೇಸರವಾಯಿತು. ಆಯ್ಕೆಗಳ ಬಗ್ಗೆ ನಮ್ಮ ತಂಡದವರು ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುತ್ತಾರೆ. ಅಲ್ಲಿಯವರೆಗೆ ನೆನಪೋಲೆ ಇಲ್ಲ.",
        "opt_out": "ಸರಿ. ಈ ಸಂಖ್ಯೆಗೆ ಮತ್ತೆ ಕರೆ ಮಾಡುವುದಿಲ್ಲ.",
        "other": "ನಿಮ್ಮ ಸಂದೇಶಕ್ಕೆ ಧನ್ಯವಾದ. ನಿಮ್ಮ ಈಗಿನ ಬಾಕಿ {due}.",
    },
}


def _layer(ctx: Ctx):
    """The teammate's SarvamVoiceLayer, using the voice key (falls back to our key)."""
    with _lock:
        if "layer" not in _layer_cache:
            if str(VOICE_DIR) not in sys.path:
                sys.path.insert(0, str(VOICE_DIR))
            from services.sarvam_voice_layer import SarvamVoiceLayer  # noqa: E402 (vendored)
            key = ctx.settings.sarvam_voice_api_key or ctx.settings.sarvam_api_key
            _layer_cache["layer"] = SarvamVoiceLayer(api_key=key, base_url=ctx.settings.sarvam_base_url) if key else None
        return _layer_cache["layer"]


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")[-10:]


def _amounts_in(text: str) -> set[int]:
    """Every rupee amount written with digits in a text, in paise."""
    out = set()
    for m in re.finditer(r"(?:₹|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text, re.IGNORECASE):
        raw = m.group(1).replace(",", "")
        if raw.count(".") <= 1 and raw.replace(".", "").isdigit():
            out.add(round(float(raw) * 100))
    return out


def guard_reply(reply: str, allowed_paise: set[int]) -> tuple[bool, str]:
    if not reply.strip():
        return False, "empty"
    if PROMISES.search(reply):
        return False, "promises money movement or a completed check"
    # Numbers in replies must be known amounts; small numbers (dates, counts) are not money.
    stray = [a for a in _amounts_in(reply) if a >= 10000 and a not in allowed_paise]
    if stray:
        return False, f"mentions an amount we cannot verify ({format_inr(stray[0])})"
    return True, ""


def process(ctx: Ctx, op, *, phone: str | None, text: str | None = None, audio: bytes | None = None,
            filename: str = "voice_note.ogg", language: str | None = None, account_id: str | None = None) -> dict:
    layer = _layer(ctx)
    if layer is None:
        raise RuntimeError("No Sarvam key configured for the customer voice layer")
    res = (layer.process_whatsapp_voice(audio_bytes=audio, filename=filename, sender_phone=phone, language_hint=language)
           if audio else layer.process_whatsapp_text(text=text or "", sender_phone=phone, language_hint=language))
    transcript = (res.get("transcript") or "").strip()
    lang = res.get("detected_language") or language or "en-IN"
    lang = lang if lang in REPLIES else "en-IN"
    entities = ((res.get("cashfree_action") or {}).get("entities") or {})
    out = {"transcript": transcript, "language": lang, "voice_layer": {
        "success": res.get("success"), "target_action": (res.get("cashfree_action") or {}).get("target_action"),
        "entities": entities, "natural_reply": res.get("reply_text")}, "claims": [], "handoffs": []}
    if not transcript:
        out.update(reply=REPLIES[lang]["other"].format(due="-"), reply_source="template", account=None)
        return out

    # --- which account is this? explicit pick, else the sender's phone (must be unique in this book)
    db = ctx.db
    acct = None
    if account_id:
        acct = db.one("SELECT * FROM accounts WHERE id=? AND merchant_id=?", (account_id, op.merchant_id))
    elif phone:
        rows = db.all("SELECT * FROM accounts WHERE merchant_id=? AND phone=?", (op.merchant_id, _digits(phone)))
        ref = (entities.get("order_id") or "").upper()
        if len(rows) > 1 and ref:
            rows = [r for r in rows if r["external_ref"].upper() == ref] or rows
        acct = rows[0] if len(rows) == 1 else None
        if len(rows) > 1:
            out["candidates"] = [r["external_ref"] for r in rows[:6]]
    out["account"] = {"id": acct["id"], "external_ref": acct["external_ref"], "display_name": acct["display_name"]} if acct else None
    if not acct:
        out.update(reply=None, reply_source="needs_account",
                   note="Could not tell which account this sender is; pick one and resend.")
        return out

    # --- record what they said as claims (never facts), then check payments they claim
    actor = f"customer:{_digits(phone) or 'unknown'}"
    today = now().astimezone(IST).date()
    sig = extract(transcript, today)
    amt, _ = corrected_amount(transcript)
    if amt is None and entities.get("amount"):
        amt = round(float(entities["amount"]) * 100)
    reference = sig.reference or entities.get("transaction_id") or None
    kinds = [k for k, on in (("opt_out", sig.opt_out), ("paid", sig.paid_claim), ("deduction", sig.deduction),
                             ("hardship", sig.hardship), ("promise", sig.promise)) if on]
    source = f"whatsapp:{_digits(phone) or 'unknown'}"
    for kind in kinds:
        promised = None
        if kind == "promise":
            promised = sig.promised_date or (today + timedelta(days=1)).isoformat()
        r = handoff(ctx, op.merchant_id, acct["id"], "reconciliation", "record_claim", actor=actor, kind=kind,
                    source=source, text=transcript, amount_paise=(sig.paid_amount_paise if kind == "paid" else
                                                                  sig.deduction_amount_paise if kind == "deduction" else amt),
                    reference=reference if kind == "paid" else None, promised_date=promised)
        out["claims"].append({"kind": kind, "claim_id": r.facts["claim_id"]})
        out["handoffs"].append({"to": "reconciliation", "action": "record_claim", "summary": r.summary})
        if kind == "paid" and reference:
            chk = handoff(ctx, op.merchant_id, acct["id"], "evidence", "check_payment_reference", actor=actor,
                          reference=reference, claim_id=r.facts["claim_id"])
            out["handoffs"].append({"to": "evidence", "action": "check_payment_reference", "summary": chk.summary})
    d = evaluate(ctx, op.merchant_id, acct["id"])
    out["decision"] = {"decision": d.decision, "code": d.code, "reason": d.reason, "outstanding_paise": d.outstanding_paise}

    # --- reply: his natural reply if it passes the guard, else a fixed one in their language
    allowed = {d.outstanding_paise, d.collectible_paise, d.disputed_paise} | _amounts_in(transcript) | ({amt} if amt else set())
    natural = res.get("reply_text") or ""
    ok, why = guard_reply(natural, allowed)
    if ok:
        out.update(reply=natural, reply_source="sarvam_natural", reply_audio_b64=res.get("reply_audio_base64"))
    else:
        key = kinds[0] if kinds else "other"
        p = next((c for c in db.all("SELECT * FROM claims WHERE account_id=? AND kind='promise' ORDER BY created_at DESC",
                                    (acct["id"],))), None)
        reply = REPLIES[lang][key].format(due=format_inr(d.outstanding_paise), date=(p or {}).get("promised_date") or "",
                                          amt=f" {format_inr(amt)}" if amt and key == "promise" else "")
        out.update(reply=reply, reply_source="template", guard_rejected=why)
        if audio and ctx.sarvam:
            try:
                out["reply_audio_b64"] = ctx.sarvam.tts(reply, lang)["audio_b64"]
            except Exception:
                pass
    ctx.db.trace(op.merchant_id, "customer_message", actor, account_id=acct["id"],
                 input={"via": "voice" if audio else "text", "transcript": transcript},
                 result={"claims": out["claims"], "decision": d.code, "reply_source": out["reply_source"]})
    return out

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
from .money import format_inr, parse_rupees_to_paise
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
        "relief": "I can't approve a waiver or refund here, but a person from our team will review your request and get back to you. Your current amount due is {due}.",
        "paid": "Thank you. We will check your payment with Cashfree and will not send reminders while we check.",
        "photo_paid": "Thanks {name}, we have your receipt{ref}. Cashfree does not show this payment yet, so reminders are paused while our team checks it. You do not need to send it again.",
        "promise": "Thank you, we have noted that you will pay{amt} by {date}.",
        "deduction": "Thank you. A person from our team will review the deduction you mentioned before anything else.",
        "hardship": "We are sorry to hear that. Someone from our team will contact you about options. No reminders until then.",
        "opt_out": "Understood. We will not call this number again.",
        "other": "Thank you for your message. Your current amount due is {due}.",
    },
    "hi-IN": {
        "relief": "मैं यहाँ छूट या रिफंड मंज़ूर नहीं कर सकता, लेकिन हमारी टीम का एक व्यक्ति आपका अनुरोध देखकर आपसे संपर्क करेगा। आपकी अभी की बकाया राशि {due} है।",
        "paid": "धन्यवाद। हम Cashfree से आपका भुगतान जाँचेंगे और जाँच तक कोई रिमाइंडर नहीं भेजेंगे।",
        "photo_paid": "धन्यवाद {name}, आपकी रसीद मिल गई{ref}। Cashfree में यह भुगतान अभी नहीं दिख रहा, इसलिए जाँच पूरी होने तक रिमाइंडर रोक दिए गए हैं। इसे दोबारा भेजने की ज़रूरत नहीं है।",
        "promise": "धन्यवाद, हमने नोट कर लिया है कि आप {date} तक{amt} भुगतान करेंगे।",
        "deduction": "धन्यवाद। आपकी बताई कटौती को हमारी टीम का एक व्यक्ति पहले देखेगा।",
        "hardship": "यह सुनकर दुख हुआ। हमारी टीम का कोई व्यक्ति विकल्पों के लिए आपसे संपर्क करेगा। तब तक कोई रिमाइंडर नहीं।",
        "opt_out": "ठीक है। हम इस नंबर पर दोबारा कॉल नहीं करेंगे।",
        "other": "आपके संदेश के लिए धन्यवाद। आपकी अभी की बकाया राशि {due} है।",
    },
    "ta-IN": {
        "relief": "தள்ளுபடி அல்லது திருப்பித் தருதலை இங்கே என்னால் அனுமதிக்க முடியாது; எங்கள் குழுவினர் ஒருவர் உங்கள் கோரிக்கையைப் பார்த்து உங்களைத் தொடர்பு கொள்வார். உங்கள் தற்போதைய நிலுவை {due}.",
        "paid": "நன்றி. உங்கள் கட்டணத்தை Cashfree-இல் சரிபார்ப்போம்; சரிபார்க்கும் வரை நினைவூட்டல் அனுப்ப மாட்டோம்.",
        "photo_paid": "நன்றி {name}, உங்கள் ரசீது கிடைத்தது{ref}. Cashfree-இல் இந்தக் கட்டணம் இன்னும் தெரியவில்லை; சரிபார்க்கும் வரை நினைவூட்டல்கள் நிறுத்தப்பட்டுள்ளன. மீண்டும் அனுப்பத் தேவையில்லை.",
        "promise": "நன்றி, நீங்கள் {date} க்குள்{amt} செலுத்துவீர்கள் என்று பதிவு செய்துள்ளோம்.",
        "deduction": "நன்றி. நீங்கள் குறிப்பிட்ட கழிவை எங்கள் குழுவினர் ஒருவர் முதலில் பார்ப்பார்.",
        "hardship": "இதைக் கேட்டு வருந்துகிறோம். மாற்று வழிகளுக்காக எங்கள் குழுவினர் உங்களைத் தொடர்பு கொள்வார். அதுவரை நினைவூட்டல் இல்லை.",
        "opt_out": "சரி. இந்த எண்ணுக்கு இனி அழைக்க மாட்டோம்.",
        "other": "உங்கள் செய்திக்கு நன்றி. உங்கள் தற்போதைய நிலுவை {due}.",
    },
    "kn-IN": {
        "relief": "ಇಲ್ಲಿ ಮನ್ನಾ ಅಥವಾ ಮರುಪಾವತಿಯನ್ನು ನಾನು ಅನುಮೋದಿಸಲು ಸಾಧ್ಯವಿಲ್ಲ; ನಮ್ಮ ತಂಡದವರು ನಿಮ್ಮ ವಿನಂತಿಯನ್ನು ಪರಿಶೀಲಿಸಿ ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುತ್ತಾರೆ. ನಿಮ್ಮ ಈಗಿನ ಬಾಕಿ {due}.",
        "paid": "ಧನ್ಯವಾದ. ನಿಮ್ಮ ಪಾವತಿಯನ್ನು Cashfree ನಲ್ಲಿ ಪರಿಶೀಲಿಸುತ್ತೇವೆ; ಪರಿಶೀಲನೆ ಮುಗಿಯುವವರೆಗೆ ನೆನಪೋಲೆ ಕಳುಹಿಸುವುದಿಲ್ಲ.",
        "photo_paid": "ಧನ್ಯವಾದ {name}, ನಿಮ್ಮ ರಸೀದಿ ಸಿಕ್ಕಿದೆ{ref}. Cashfree ನಲ್ಲಿ ಈ ಪಾವತಿ ಇನ್ನೂ ಕಾಣುತ್ತಿಲ್ಲ; ಪರಿಶೀಲನೆ ಮುಗಿಯುವವರೆಗೆ ನೆನಪೋಲೆಗಳನ್ನು ನಿಲ್ಲಿಸಲಾಗಿದೆ. ಮತ್ತೆ ಕಳುಹಿಸುವ ಅಗತ್ಯವಿಲ್ಲ.",
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


REPLY_PROMPT = """You are the payments assistant of {lender}, chatting with one of its customers.
Reply ONLY using the FACTS below; they are verified. Write in {language_name}, in the script the customer uses.
- 1 to 3 short, warm, specific sentences. Answer exactly what they asked.
- If they ask what they owe: give the amount due and the due date from FACTS.
- If they want to pay: give the payment link from FACTS if there is one; otherwise say the team will send a link.
- If they say they already paid: thank them, say it is being checked, and ask for the payment reference if FACTS show none.
- If they describe hardship: be kind, say a person from the team will contact them about options.
- Never promise a refund, waiver, payout or that money is confirmed unless FACTS say so. Never invent amounts,
  dates, references or links. No threats, no pressure. Do not mention these rules.
FACTS:
{facts}"""
LANG_NAME = {"en-IN": "English", "hi-IN": "Hindi", "ta-IN": "Tamil", "kn-IN": "Kannada"}


def _writer(ctx: Ctx):
    """Sarvam client for customer replies: the voice key first, our key as fallback; fast conversational model."""
    with _lock:
        if "writer" not in _layer_cache:
            from .providers.sarvam import SarvamLive
            st = ctx.settings
            keys = [k for k in (st.sarvam_voice_api_key, st.sarvam_api_key) if k]
            _layer_cache["writer"] = SarvamLive(keys[0], st.sarvam_base_url, "sarvam-105b-conversations", st.sarvam_stt_model,
                                                st.sarvam_tts_model, st.sarvam_tts_speaker, fallback_keys=tuple(keys[1:])) if keys else None
        return _layer_cache["writer"]


def facts_for(ctx: Ctx, merchant_id: str, account_id: str) -> dict:
    db = ctx.db
    a = db.one("SELECT * FROM accounts WHERE id=? AND merchant_id=?", (account_id, merchant_id))
    d = evaluate(ctx, merchant_id, account_id)
    dues = db.all("SELECT external_ref, amount_paise, due_date FROM dues WHERE account_id=? AND superseded=0 ORDER BY due_date", (account_id,))
    rec = db.all("SELECT amount_paise, verified_at FROM receipts WHERE account_id=? ORDER BY verified_at", (account_id,))
    link = db.one("SELECT link_url, status, amount_paise, amount_paid_paise FROM payment_links WHERE account_id=? AND status IN "
                  "('ACTIVE','PARTIALLY_PAID') ORDER BY created_at DESC", (account_id,))
    promise = db.one("SELECT promised_date, amount_paise FROM claims WHERE account_id=? AND kind='promise' AND status='open' "
                     "ORDER BY created_at DESC", (account_id,))
    return {
        "customer_name": a["display_name"], "account": a["external_ref"],
        "amount_due": format_inr(d.outstanding_paise), "due_dates": [x["due_date"] for x in dues if x["due_date"]],
        "payments_confirmed_by_cashfree": [format_inr(r["amount_paise"]) for r in rec],
        "payment_link": ({"url": link["link_url"], "amount": format_inr(link["amount_paise"] - link["amount_paid_paise"])}
                         if link and link["link_url"] and link["link_url"].startswith("http") else None),
        "promise_on_record": {"date": promise["promised_date"], "amount": format_inr(promise["amount_paise"]) if promise["amount_paise"] else None} if promise else None,
        "account_status": d.code, "status_explained": d.reason, "do_not_call": bool(a["do_not_contact"]),
        "_allowed_paise": sorted({d.outstanding_paise, d.collectible_paise, d.disputed_paise}
                                 | {r["amount_paise"] for r in rec} | {x["amount_paise"] for x in dues}
                                 | ({link["amount_paise"], link["amount_paise"] - link["amount_paid_paise"]} if link else set())),
    }


def history(ctx: Ctx, merchant_id: str, account_id: str, limit: int = 20) -> list[dict]:
    import json as _json
    rows = ctx.db.all("SELECT input_json, result_json, ts FROM trace WHERE merchant_id=? AND account_id=? AND kind='customer_message' "
                      "ORDER BY id DESC LIMIT ?", (merchant_id, account_id, limit))
    out = []
    for r in reversed(rows):
        i, res = _json.loads(r["input_json"] or "{}"), _json.loads(r["result_json"] or "{}")
        out.append({"ts": r["ts"], "via": i.get("via"), "text": i.get("transcript"), "reply": res.get("reply"),
                    "reply_source": res.get("reply_source"), "claims": [c["kind"] for c in res.get("claims", [])],
                    "status": res.get("decision"), "language": res.get("language")})
    return out


def write_reply(ctx: Ctx, merchant_id: str, account_id: str, message: str, lang: str) -> tuple[str | None, str]:
    """A grounded reply from Sarvam; returns (reply, reason) where reply is None if it failed the guard."""
    w = _writer(ctx)
    if not w:
        return None, "no Sarvam key"
    f = facts_for(ctx, merchant_id, account_id)
    allowed = set(f.pop("_allowed_paise")) | _amounts_in(message)
    import json as _json
    msgs = [{"role": "system", "content": REPLY_PROMPT.format(lender=ctx.settings.lender_display_name,
                                                             language_name=LANG_NAME.get(lang, "English"),
                                                             facts=_json.dumps(f, ensure_ascii=False, indent=1))}]
    for h in history(ctx, merchant_id, account_id, limit=6):
        if h["text"]:
            msgs.append({"role": "user", "content": h["text"]})
        if h["reply"]:
            msgs.append({"role": "assistant", "content": h["reply"]})
    msgs.append({"role": "user", "content": message})
    try:
        r = w.chat(msgs)
    except Exception as e:  # network or provider failure: fall back
        return None, f"writer failed: {e.__class__.__name__}"
    text = ((r.get("message") or {}).get("content") or "").strip()
    ok, why = guard_reply(text, allowed)
    return (text, "") if ok else (None, why)


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")[-10:]


MONEY = re.compile(
    r"(?:₹|\brs\.?|\binr)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"                       # ₹4,200 / Rs 4200 / INR 4200
    r"|([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:rupees?|रुपये|रुपए|रुपया|ரூபாய்|ರೂಪಾಯಿ)",   # 4200 rupees / 4200 रुपये
    re.IGNORECASE)


def _amounts_in(text: str) -> set[int]:
    """Rupee amounts in a text, in paise. Only numbers marked as money count, so dates and IDs are ignored."""
    out = set()
    for m in MONEY.finditer(text or ""):
        raw = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            out.add(round(float(raw) * 100))
        except ValueError:
            pass
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


RECEIPT_WORDS = re.compile(r"success|successful|paid|completed|debited|credited|receipt|transaction|transferred|"
                           r"सफल|भुगतान|வெற்றி|ಯಶಸ್ವಿ", re.IGNORECASE)


def read_image(ctx: Ctx, data: bytes, filename: str) -> dict:
    """Sarvam Document AI OCR (teammate module, ocr/). Tries our key, then the second key."""
    from ocr.sarvam_ocr import SarvamOCR
    last = None
    for key in (ctx.settings.sarvam_api_key, ctx.settings.sarvam_voice_api_key):
        if not key:
            continue
        try:
            r = SarvamOCR(api_key=key).process_document(data, filename)
            if r.get("extracted_text", "").strip() or not r.get("errors"):
                return r
            last = r
        except Exception as e:  # quota or network: try the other key
            last = {"extracted_text": "", "errors": [type(e).__name__]}
    if last is None:
        raise RuntimeError("No Sarvam key configured for OCR")
    return last


def process(ctx: Ctx, op, *, phone: str | None, text: str | None = None, audio: bytes | None = None,
            filename: str = "voice_note.ogg", language: str | None = None, account_id: str | None = None,
            image: bytes | None = None, image_name: str = "photo.jpg") -> dict:
    layer = _layer(ctx)
    if layer is None:
        raise RuntimeError("No Sarvam key configured for the customer voice layer")
    ocr = None
    if image:
        ocr = read_image(ctx, image, image_name)
        read = re.sub(r"\n\s*\n+", "\n", (ocr.get("extracted_text") or "").strip())
        ocr["extracted_text"] = read
        text = "\n".join(x for x in ((text or "").strip(), read) if x)
        if not text:
            return {"transcript": "", "language": language or "en-IN", "claims": [], "handoffs": [], "account": None,
                    "ocr": {"text": "", "languages": [], "review_required": True},
                    "reply": "I could not read that photo. Please send a clearer picture of the receipt or note.",
                    "reply_source": "template"}
    res = (layer.process_whatsapp_voice(audio_bytes=audio, filename=filename, sender_phone=phone, language_hint=language)
           if audio else layer.process_whatsapp_text(text=text or "", sender_phone=phone, language_hint=language))
    transcript = (res.get("transcript") or "").strip()
    lang = res.get("detected_language") or language or "en-IN"
    lang = lang if lang in REPLIES else "en-IN"
    entities = ((res.get("cashfree_action") or {}).get("entities") or {})
    out = {"transcript": transcript, "language": lang, "voice_layer": {
        "success": res.get("success"), "target_action": (res.get("cashfree_action") or {}).get("target_action"),
        "entities": entities, "natural_reply": res.get("reply_text")}, "claims": [], "handoffs": []}
    if ocr is not None:
        transcript = transcript or (text or "")
        out["transcript"] = transcript
        out["ocr"] = {"text": (ocr.get("extracted_text") or "").strip(), "languages": ocr.get("detected_languages", [])[:3],
                      "review_required": bool(ocr.get("review_required"))}
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
    if ocr is not None and not sig.reference:  # receipts put the id on its own line, under a label
        long_id = re.search(r"\b\d{10,22}\b", transcript)
        sig.reference = entities.get("transaction_id") or (long_id.group(0) if long_id else None)
    if ocr is not None and not sig.paid_claim and sig.reference and RECEIPT_WORDS.search(transcript):
        sig.paid_claim = True  # a photo of a payment receipt with a reference counts as "I paid" (still only a claim)
    if ocr is not None:  # on a receipt, the paid amount is the first ₹/Rs figure, never a long transaction id
        m = MONEY.search(transcript)
        try:
            sig.paid_amount_paise = parse_rupees_to_paise(m.group(1) or m.group(2)) if m else None
        except Exception:
            sig.paid_amount_paise = None
    amt, _ = corrected_amount(transcript)
    if amt is None and entities.get("amount"):
        amt = round(float(entities["amount"]) * 100)
    reference = sig.reference or entities.get("transaction_id") or None
    kinds = [k for k, on in (("opt_out", sig.opt_out), ("paid", sig.paid_claim), ("deduction", sig.deduction),
                             ("hardship", sig.hardship), ("promise", sig.promise)) if on]
    source = f"{'photo' if ocr is not None else 'chat'}:{_digits(phone) or 'unknown'}"
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

    # --- reply: grounded in this account's verified facts; then the voice layer's draft; then a fixed reply
    message = (f"[The customer sent a photo; do not ask them to send it again. Text read from it:]\n{transcript}"
               if ocr is not None else transcript)
    grounded, why = write_reply(ctx, op.merchant_id, acct["id"], message, lang)
    if grounded:
        out.update(reply=grounded, reply_source="grounded")
        if audio:
            w = _writer(ctx)
            try:
                out["reply_audio_b64"] = w.tts(grounded, lang)["audio_b64"] if w else None
            except Exception:
                pass
    else:  # the voice layer's own draft is not used as a reply: it cannot see the account, so it asks vague questions
        asks_relief = re.search(r"waive|waiver|refund|discount|माफ|छूट|रिफंड|தள்ளுபடி|ரீஃபண்ட்|ಮನ್ನಾ|ರಿಯಾಯಿತಿ|ಮರುಪಾವತಿ", transcript, re.IGNORECASE)
        key = "relief" if asks_relief else (kinds[0] if kinds else "other")
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
    # A receipt photo that Cashfree cannot confirm yet: a fixed, clear answer (models tend to ask for the receipt again).
    if ocr is not None and "paid" in kinds and d.code in ("PAID_CLAIM_UNVERIFIED", "PAID_CLAIM_CHECKING", "EVIDENCE_CONFLICT"):
        out.update(reply=REPLIES[lang]["photo_paid"].format(name=acct["display_name"] or "",
                                                           ref=f" (ref {reference})" if reference else ""),
                   reply_source="template")
        out.pop("reply_audio_b64", None)
    ctx.db.trace(op.merchant_id, "customer_message", actor, account_id=acct["id"],
                 input={"via": "image" if ocr is not None else "voice" if audio else "text", "transcript": transcript},
                 result={"claims": out["claims"], "decision": d.code, "reply_source": out["reply_source"],
                         "reply": out.get("reply"), "language": lang})
    return out

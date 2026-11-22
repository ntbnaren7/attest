import os
import json
import re
import logging
from typing import Optional, Dict, Any, Union
import requests

from config import settings
from services.sarvam_service import SarvamService

logger = logging.getLogger("sarvam_voice_layer")
logging.basicConfig(level=logging.INFO)

SYSTEM_PROMPT = """You are an expert conversational AI for an enterprise payment system powered by Cashfree and WhatsApp.
Your role:
1. Analyze incoming customer/merchant messages (transcribed voice or text).
2. Extract all financial entities (amounts, order IDs, transaction IDs, UPI VPAs, phone numbers).
3. Classify the user's intent and select the appropriate Cashfree action:
   - "payment_recovery": Money deducted but order failed, payment retry link request, failed checkout, refund request. (Priority: HIGH or CRITICAL)
   - "reconciliation": Ledger vs gateway reconciliation, bank statement comparison, UTR verification, settlement sync.
   - "leakage_detection": MDR fee audit, double refund check, commission fee leakage, unauthorized fee deductions.
   - "payout_management": Vendor payouts, partner transfers, UPI VPA / bank account verification, balance inquiries.
   - "customer_support": General questions, greeting, or unclassified requests.
4. Formulate a NATURAL, EMPATHETIC, QUESTION-SPECIFIC reply to speak or send back to the user.

CRITICAL RULES FOR "reply_text":
- NEVER use generic repetitive template phrases like:
  - "I understand your concern. I will check the status of your ₹2500 payment right away."
  - "Sure, I will look into it right away."
  - "Your request has been received."
- Instead, craft a human, empathetic response tailored directly to the specific problem, numbers, and context:
  - Mention specific amounts, order IDs, or beneficiary details when provided.
  - If money was debited and order failed: Express reassuring empathy and state that you're checking with Cashfree and securing their funds.
  - If vendor payout: Confirm the exact amount and VPA / account with positive, efficient tone.
  - If reconciliation or leakage audit: Provide a professional, technically accurate confirmation that the ledger / transaction audit has started.
  - Language matching:
    - If user speaks/writes in Hindi or Hinglish, reply in natural, polite conversational Hindi.
    - If English, reply in fluent, conversational English.
    - If Tamil, Telugu, Kannada, Bengali, etc., reply in that language.
  - Length: 1 to 2 clear, natural sentences suitable for a WhatsApp message or audio voice note.

OUTPUT FORMAT:
Return ONLY valid JSON (no markdown backticks, no preamble):
{
  "detected_language": "<hi-IN | en-IN | ta-IN | te-IN | bn-IN | mr-IN | kn-IN>",
  "intent": "<RECOVER_FAILED_PAYMENT | CREATE_PAYMENT_LINK | CHECK_PAYMENT_STATUS | INITIATE_REFUND | RECONCILE_TRANSACTIONS | CHECK_SETTLEMENT | DETECT_FEE_LEAKAGE | AUDIT_MDR_CHARGES | INITIATE_PAYOUT | VERIFY_BENEFICIARY | CHECK_PAYOUT_BALANCE | GENERAL_QUERY>",
  "target_action": "<payment_recovery | reconciliation | leakage_detection | payout_management | customer_support>",
  "priority": "<CRITICAL | HIGH | MEDIUM | LOW>",
  "entities": {
    "order_id": "<string or null>",
    "transaction_id": "<string or null>",
    "amount": <number or null>,
    "currency": "INR",
    "customer_phone": "<string or null>",
    "customer_email": "<string or null>",
    "beneficiary_vpa": "<string or null>",
    "beneficiary_account": "<string or null>",
    "beneficiary_ifsc": "<string or null>",
    "gateway": "Cashfree"
  },
  "action_prompt": "<Concise, actionable task description for the Cashfree agent/API relay>",
  "reply_text": "<Natural, empathetic, question-specific response tailored to this exact message>",
  "confidence_score": 0.98
}"""

class SarvamVoiceLayer:
    """
    Dedicated Sarvam Voice & Intelligence Layer for WhatsApp & Python API backend.
    
    Responsibilities:
    - Ingest WhatsApp voice notes (OGG/Opus, MP3, WAV, M4A) or text messages.
    - Multilingual Speech-to-Text via Sarvam Saaras (saaras:v3).
    - Intelligence, entity extraction & conversational reply generation via Sarvam 105B (sarvam-105b-conversations).
    - Mode-matched response:
      * Voice in -> Voice out (Sarvam Bulbul TTS audio base64 + text).
      * Text in  -> Text out (Text only, TTS skipped for sub-second speed).
    - Clean, standardized payload for Cashfree agents or API relay.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.base_url = (base_url or settings.SARVAM_BASE_URL).rstrip("/")
        self.sarvam_service = SarvamService(api_key=self.api_key, base_url=self.base_url)

    # =========================================================
    # 1. PROCESS INCOMING WHATSAPP VOICE MESSAGE
    # =========================================================
    def process_whatsapp_voice(
        self,
        audio_bytes: Optional[bytes] = None,
        file_path: Optional[str] = None,
        filename: str = "whatsapp_voice.ogg",
        sender_phone: Optional[str] = None,
        language_hint: Optional[str] = None,
        tts_speaker: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Processes an incoming voice message from WhatsApp:
        1. Transcribes audio using Sarvam Saaras STT (handles OGG/Opus, MP3, WAV, etc.)
        2. Analyzes transcript with Sarvam 105B Conversations LLM for entities & intent
        3. Formulates a natural, question-specific response
        4. Synthesizes response speech using Sarvam Bulbul TTS into base64 audio
        5. Prepares Cashfree action payload for downstream agents
        """
        # Step 1: Transcribe audio
        try:
            stt_res = self.sarvam_service.transcribe_audio(
                audio_bytes=audio_bytes,
                file_path=file_path,
                filename=filename,
                language_code=language_hint
            )
            raw_transcript = (stt_res.transcript or "").strip()
            detected_lang = stt_res.language_code or language_hint or "en-IN"
        except Exception as e:
            logger.error(f"Sarvam STT failed: {e}")
            return self._build_error_response(
                input_type="voice",
                sender_phone=sender_phone,
                error_message=f"Audio transcription error: {str(e)}",
                generate_audio=True
            )

        # Handle empty/silent audio
        if not raw_transcript:
            if detected_lang and detected_lang.startswith("hi"):
                fallback_text = "माफ़ कीजिये, आपकी आवाज़ साफ़ नहीं आई। कृपया अपना संदेश दोबारा बोलें।"
            elif detected_lang and detected_lang.startswith("ta"):
                fallback_text = "மன்னிக்கவும், உங்கள் குரல் தெளிவாகக் கேட்கவில்லை. தயவுசெய்து மீண்டும் பேசவும்."
            elif detected_lang and detected_lang.startswith("kn"):
                fallback_text = "ಕ್ಷಮಿಸಿ, ನಿಮ್ಮ ಧ್ವನಿ ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಮಾತನಾಡಿ."
            elif detected_lang and detected_lang.startswith("te"):
                fallback_text = "క్షమించండి, మీ వాయిస్ స్పష్టంగా వినబడలేదు. దయచేసి మళ్ళీ చెప్పండి."
            else:
                fallback_text = "I'm sorry, I couldn't hear any speech clearly. Could you please repeat your query?"

            audio_b64 = self._synthesize_audio_safely(
                text=fallback_text,
                language_code=detected_lang,
                speaker=tts_speaker
            )
            return {
                "success": False,
                "input_type": "voice",
                "sender_phone": sender_phone,
                "transcript": "",
                "detected_language": detected_lang,
                "reply_mode": "voice",
                "reply_text": fallback_text,
                "reply_audio_base64": audio_b64,
                "reply_audio_mime": "audio/wav" if audio_b64 else None,
                "cashfree_action": None,
                "message": "Silent or empty audio received."
            }

        # Step 2: Extract entities & generate dynamic conversational reply
        extraction = self._run_llm_intelligence(
            text=raw_transcript,
            language_hint=detected_lang,
            sender_phone=sender_phone
        )

        reply_text = extraction.get("reply_text") or "I've received your request and am verifying it with Cashfree."
        final_lang = extraction.get("detected_language") or detected_lang

        # Step 3: Synthesize voice response via Bulbul TTS (Voice input -> Voice output)
        audio_b64 = self._synthesize_audio_safely(
            text=reply_text,
            language_code=final_lang,
            speaker=tts_speaker
        )

        return {
            "success": True,
            "input_type": "voice",
            "sender_phone": sender_phone,
            "transcript": raw_transcript,
            "detected_language": final_lang,
            "reply_mode": "voice",
            "reply_text": reply_text,
            "reply_audio_base64": audio_b64,
            "reply_audio_mime": "audio/wav" if audio_b64 else None,
            "cashfree_action": {
                "target_action": extraction.get("target_action"),
                "priority": extraction.get("priority"),
                "intent": extraction.get("intent"),
                "entities": extraction.get("entities"),
                "action_prompt": extraction.get("action_prompt"),
                "confidence_score": extraction.get("confidence_score")
            }
        }

    # =========================================================
    # 2. PROCESS INCOMING WHATSAPP TEXT MESSAGE
    # =========================================================
    def process_whatsapp_text(
        self,
        text: str,
        sender_phone: Optional[str] = None,
        language_hint: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Processes an incoming text message from WhatsApp:
        1. Analyzes text with Sarvam 105B Conversations LLM for entities & intent
        2. Formulates a natural, question-specific response
        3. Skips TTS audio (Text in -> Text out) for ultra-low latency (<1.5s)
        4. Prepares Cashfree action payload for downstream agents
        """
        clean_text = (text or "").strip()
        if not clean_text:
            return {
                "success": False,
                "input_type": "text",
                "sender_phone": sender_phone,
                "transcript": "",
                "detected_language": language_hint or "en-IN",
                "reply_mode": "text",
                "reply_text": "Please provide a valid question or payment detail.",
                "reply_audio_base64": None,
                "reply_audio_mime": None,
                "cashfree_action": None,
                "message": "Empty text received."
            }

        # Step 1: Extract entities & generate dynamic conversational reply
        extraction = self._run_llm_intelligence(
            text=clean_text,
            language_hint=language_hint,
            sender_phone=sender_phone
        )

        reply_text = extraction.get("reply_text") or "I've received your request and am verifying it with Cashfree."
        final_lang = extraction.get("detected_language") or language_hint or "en-IN"

        return {
            "success": True,
            "input_type": "text",
            "sender_phone": sender_phone,
            "transcript": clean_text,
            "detected_language": final_lang,
            "reply_mode": "text",
            "reply_text": reply_text,
            "reply_audio_base64": None,
            "reply_audio_mime": None,
            "cashfree_action": {
                "target_action": extraction.get("target_action"),
                "priority": extraction.get("priority"),
                "intent": extraction.get("intent"),
                "entities": extraction.get("entities"),
                "action_prompt": extraction.get("action_prompt"),
                "confidence_score": extraction.get("confidence_score")
            }
        }

    # =========================================================
    # 3. UNIFIED INTAKE HELPER
    # =========================================================
    def process_incoming_message(
        self,
        input_type: str,
        content: Union[str, bytes],
        filename: Optional[str] = None,
        sender_phone: Optional[str] = None,
        language_hint: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Unified router for incoming WhatsApp webhook payloads:
        - input_type="voice": processes audio bytes or audio path
        - input_type="text": processes text string
        """
        mode = input_type.lower().strip()
        if mode in ["voice", "audio"]:
            if isinstance(content, str):
                return self.process_whatsapp_voice(
                    file_path=content,
                    filename=filename or os.path.basename(content),
                    sender_phone=sender_phone,
                    language_hint=language_hint
                )
            else:
                return self.process_whatsapp_voice(
                    audio_bytes=content,
                    filename=filename or "whatsapp_voice.ogg",
                    sender_phone=sender_phone,
                    language_hint=language_hint
                )
        else:
            text_str = content.decode("utf-8") if isinstance(content, bytes) else str(content)
            return self.process_whatsapp_text(
                text=text_str,
                sender_phone=sender_phone,
                language_hint=language_hint
            )

    # =========================================================
    # 4. INTERNAL LLM INTELLIGENCE & ENTITY EXTRACTION
    # =========================================================
    def _run_llm_intelligence(
        self,
        text: str,
        language_hint: Optional[str] = None,
        sender_phone: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calls Sarvam 105B Conversations to extract structured entities and craft a natural reply.
        Fast-fails to intelligent rule-based fallback if no API key is configured or on network failure.
        """
        if not self.api_key:
            return self._fallback_extraction(text, sender_phone, language_hint=language_hint)

        endpoint = f"{self.base_url}/v1/chat/completions"
        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json"
        }

        user_content = f'User Message: "{text}"'
        if sender_phone:
            user_content += f'\nSender Phone: {sender_phone}'

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ]
        if language_hint:
            messages.append({"role": "system", "content": f"Spoken/Detected language hint: {language_hint}"})

        payload = {
            "model": settings.SARVAM_LLM_MODEL,  # sarvam-105b-conversations
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1024
        }

        try:
            res = self.sarvam_service.session.post(endpoint, headers=headers, json=payload, timeout=45)
            if res.status_code != 200:
                logger.error(f"Sarvam LLM Error ({res.status_code}): {res.text}")
                return self._fallback_extraction(text, sender_phone, language_hint=language_hint)

            res_json = res.json()
            choice = res_json["choices"][0]
            msg_data = choice.get("message", {})
            raw_content = msg_data.get("content") or msg_data.get("reasoning_content") or ""
            raw_content = raw_content.strip()

            parsed = self._clean_and_parse_json(raw_content)
            if not parsed or not isinstance(parsed, dict):
                return self._fallback_extraction(text, sender_phone, language_hint=language_hint)

            # Ensure entities dict exists
            entities = parsed.get("entities") or {}
            if sender_phone and not entities.get("customer_phone"):
                entities["customer_phone"] = sender_phone
            parsed["entities"] = entities

            # Guard against empty reply_text
            if not parsed.get("reply_text"):
                parsed["reply_text"] = self._generate_contextual_fallback_reply(parsed, text)

            return parsed

        except Exception as e:
            logger.error(f"Sarvam LLM extraction exception: {e}")
            return self._fallback_extraction(text, sender_phone, language_hint=language_hint)

    # =========================================================
    # 5. AUDIO SYNTHESIS HELPER
    # =========================================================
    def _synthesize_audio_safely(
        self,
        text: str,
        language_code: Optional[str] = "en-IN",
        speaker: Optional[str] = None
    ) -> Optional[str]:
        """
        Generates TTS base64 audio via Sarvam Bulbul. Fails gracefully to None on error.
        """
        target_lang = self._normalize_tts_language(language_code)
        try:
            tts_res = self.sarvam_service.generate_speech(
                text=text,
                target_language_code=target_lang,
                speaker=speaker or settings.SARVAM_DEFAULT_TTS_SPEAKER
            )
            return tts_res.audio_base64
        except Exception as e:
            logger.warning(f"Failed to synthesize Bulbul TTS audio: {e}")
            return None

    def _normalize_tts_language(self, lang: Optional[str]) -> str:
        if not lang:
            return "en-IN"
        l = lang.lower()
        if "hi" in l:
            return "hi-IN"
        if "ta" in l:
            return "ta-IN"
        if "te" in l:
            return "te-IN"
        if "kn" in l:
            return "kn-IN"
        if "mr" in l:
            return "mr-IN"
        if "bn" in l:
            return "bn-IN"
        if "gu" in l:
            return "gu-IN"
        if "ml" in l:
            return "ml-IN"
        if "pa" in l:
            return "pa-IN"
        if "od" in l:
            return "od-IN"
        return "en-IN"

    # =========================================================
    # 6. HELPERS: JSON PARSER & CONTEXTUAL FALLBACKS
    # =========================================================
    def _detect_language(self, text: str, hint: Optional[str] = None) -> str:
        """Detect language code from hint, Unicode scripts, or multilingual phrasing."""
        if hint and hint != "unknown":
            return hint
        if re.search(r"[\u0900-\u097F]", text):
            return "hi-IN"
        if re.search(r"[\u0B80-\u0BFF]", text):
            return "ta-IN"
        if re.search(r"[\u0C80-\u0CFF]", text):
            return "kn-IN"
        if re.search(r"[\u0C00-\u0C7F]", text):
            return "te-IN"
        if re.search(r"[\u0980-\u09FF]", text):
            return "bn-IN"
        if re.search(r"[\u0D00-\u0D7F]", text):
            return "ml-IN"
        t_lower = text.lower()
        if any(w in t_lower for w in ["maine", "kar diya", "bhai", "paisa", "paise", "de dunga", "bheja", "aayega", "nahi", "rupaye", "kripya", "kab"]):
            return "hi-IN"
        if any(w in t_lower for w in ["panam", "kuduthen", "anupiten", "eppo"]):
            return "ta-IN"
        if any(w in t_lower for w in ["kotte", "kottiddini", "duddu"]):
            return "kn-IN"
        return "en-IN"

    def _clean_and_parse_json(self, content: str) -> Optional[Dict[str, Any]]:
        # Strip reasoning models <think> tags if present
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"^```\s*$", "", cleaned, flags=re.MULTILINE).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    pass
        return None

    def _fallback_extraction(
        self,
        text: str,
        sender_phone: Optional[str] = None,
        language_hint: Optional[str] = None
    ) -> Dict[str, Any]:
        """Provides smart rule-based extraction with multilingual and entity support."""
        t_lower = text.lower()
        detected_lang = self._detect_language(text, hint=language_hint)

        target_action = "customer_support"
        priority = "MEDIUM"
        intent = "GENERAL_QUERY"

        if any(w in t_lower for w in ["fail", "deduct", "cut", "debit", "lost", "recover", "retry", "refund", "already paid", "paid", "diya", "bhar diya"]):
            target_action = "payment_recovery"
            priority = "HIGH"
            intent = "CHECK_PAYMENT_STATUS" if ("already" in t_lower or "paid" in t_lower or "diya" in t_lower) else "RECOVER_FAILED_PAYMENT"
        elif any(w in t_lower for w in ["reconcil", "ledger", "utr", "mismatch", "settle", "statement"]):
            target_action = "reconciliation"
            priority = "MEDIUM"
            intent = "RECONCILE_TRANSACTIONS"
        elif any(w in t_lower for w in ["leak", "mdr", "fee", "charge", "commission"]):
            target_action = "leakage_detection"
            priority = "MEDIUM"
            intent = "DETECT_FEE_LEAKAGE"
        elif any(w in t_lower for w in ["payout", "transfer", "vpa", "vendor", "disburse"]):
            target_action = "payout_management"
            priority = "MEDIUM"
            intent = "INITIATE_PAYOUT"

        # 1. Order / Invoice / Account Reference: CFORD-..., ORD_..., INV-..., L-1001, B-501
        order_match = re.search(r"\b((?:CFORD|ORD|INV|REF)[-_]?[A-Za-z0-9]+)\b", text, re.IGNORECASE)
        if not order_match:
            order_match = re.search(r"\b([A-Z]{1,3}-\d{3,6})\b", text)

        # 2. UTR / Transaction ID (12-digit UPI reference or explicit UTR/TXN prefix)
        txn_match = re.search(r"\b(?:utr|txn|rrn|txnid|transaction(?:_id)?|ref(?:erence)?)[:\s#]*([A-Za-z0-9]{10,24})\b", text, re.IGNORECASE)
        if not txn_match:
            txn_match = re.search(r"\b(\d{12})\b", text)

        # 3. Amount
        amount_match = re.search(r"(?:₹|rs\.?|inr)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:₹|rupees|rs)?", text, re.IGNORECASE)
        vpa_match = re.search(r"[\w\.\-]+@[\w\-]+", text)

        amount = None
        if amount_match:
            try:
                parsed_amt = float(amount_match.group(1).replace(",", ""))
                # Prevent UTR / phone numbers from being treated as amounts
                if parsed_amt < 100_000_000:
                    amount = parsed_amt
            except Exception:
                pass

        entities = {
            "order_id": order_match.group(1).upper() if order_match else None,
            "transaction_id": txn_match.group(1) if txn_match else None,
            "amount": amount,
            "currency": "INR",
            "customer_phone": sender_phone,
            "beneficiary_vpa": vpa_match.group(0) if vpa_match else None,
            "gateway": "Cashfree"
        }

        reply = self._generate_contextual_fallback_reply({
            "target_action": target_action,
            "entities": entities,
            "detected_language": detected_lang
        }, text)

        return {
            "detected_language": detected_lang,
            "intent": intent,
            "target_action": target_action,
            "priority": priority,
            "entities": entities,
            "action_prompt": f"Process {target_action} request for user inquiry.",
            "reply_text": reply,
            "confidence_score": 0.85
        }

    def _generate_contextual_fallback_reply(self, data: Dict[str, Any], raw_text: str) -> str:
        entities = data.get("entities") or {}
        order_id = entities.get("order_id")
        amount = entities.get("amount")
        action = data.get("target_action")
        lang = data.get("detected_language") or "en-IN"

        amt_str = f"₹{amount:,.0f}" if amount else ("your payment" if not lang.startswith("hi") else "आपका भुगतान")
        ord_str = f" for order {order_id}" if order_id else ""

        if lang.startswith("hi"):
            if action == "payment_recovery":
                ord_hi = f" (ऑर्डर {order_id})" if order_id else ""
                return f"हम आपके {amt_str}{ord_hi} के भुगतान की स्थिति Cashfree पर जांच रहे हैं। पुष्टि होते ही आपको सूचित किया जाएगा।"
            elif action == "reconciliation":
                return f"आपके लेनदेन का हमारे लेजर और Cashfree रिकॉर्ड के साथ मिलान (reconciliation) शुरू कर दिया गया है।"
            elif action == "leakage_detection":
                return f"लेनदेन और MDR शुल्कों की समीक्षा की जा रही है।"
            elif action == "payout_management":
                return f"Cashfree पेआउट और लाभार्थी विवरण की पुष्टि की जा रही है।"
            return "आपका संदेश प्राप्त हुआ है, हमारी टीम विवरण की जांच कर रही है।"

        if action == "payment_recovery":
            return f"I'm checking the status of {amt_str}{ord_str} with Cashfree right now. If the payment failed, I will provide a direct recovery link immediately."
        elif action == "reconciliation":
            return f"Initiated reconciliation for {ord_str or 'the requested transaction'} against our ledger and Cashfree settlement records."
        elif action == "leakage_detection":
            return f"Running our revenue leakage detector to audit fee deductions and MDR charges for this transaction."
        elif action == "payout_management":
            vpa = entities.get("beneficiary_vpa")
            vpa_str = f" to {vpa}" if vpa else ""
            return f"Preparing the Cashfree payout of {amt_str}{vpa_str} and verifying beneficiary details now."
        return "Your request has been received and our team is verifying the payment details with Cashfree."

    def _build_error_response(
        self,
        input_type: str,
        sender_phone: Optional[str],
        error_message: str,
        generate_audio: bool = False,
        language_code: Optional[str] = "en-IN"
    ) -> Dict[str, Any]:
        lang = language_code or "en-IN"
        if lang.startswith("hi"):
            reply_text = "तकनीकी समस्या के कारण प्रक्रिया पूरी नहीं हो सकी। कृपया अपना संदेश दोबारा भेजें।"
        else:
            reply_text = "We encountered a temporary processing issue. Please try sending your message again."
        audio_b64 = None
        if generate_audio:
            audio_b64 = self._synthesize_audio_safely(reply_text, lang)

        return {
            "success": False,
            "input_type": input_type,
            "sender_phone": sender_phone,
            "transcript": "",
            "detected_language": lang,
            "reply_mode": "voice" if generate_audio else "text",
            "reply_text": reply_text,
            "reply_audio_base64": audio_b64,
            "reply_audio_mime": "audio/wav" if audio_b64 else None,
            "cashfree_action": None,
            "error": error_message
        }

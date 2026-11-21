import os
import json
import re
import logging
from typing import Optional, Dict, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from config import settings
from schemas import (
    VoiceAgentExtraction,
    FinancialEntities,
    AgentTarget,
    PriorityLevel,
    IntentCategory,
    AudioTranscriptionResponse,
    SpeechSynthesisResponse,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class SarvamService:
    """
    Service wrapper for Sarvam AI APIs:
    - Saaras (STT): Multilingual Speech-to-Text
    - Sarvam-105b (LLM): Multilingual Structured Entity & Intent Extractor
    - Bulbul (TTS): Multilingual Text-to-Speech
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.base_url = (base_url or settings.SARVAM_BASE_URL).rstrip("/")
        self.headers = {
            "api-subscription-key": self.api_key
        }
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ==========================================
    # 1. SPEECH-TO-TEXT (Saaras)
    # ==========================================
    def transcribe_audio(
        self,
        audio_bytes: Optional[bytes] = None,
        file_path: Optional[str] = None,
        filename: str = "voice_input.wav",
        language_code: Optional[str] = None,
        model: Optional[str] = None,
    ) -> AudioTranscriptionResponse:
        """
        Transcribes speech audio using Sarvam Saaras model.
        Supports wav, mp3, ogg, aac audio.
        """
        stt_model = model or settings.SARVAM_STT_MODEL
        endpoint = f"{self.base_url}/speech-to-text"

        if file_path:
            with open(file_path, "rb") as f:
                content = f.read()
            filename = os.path.basename(file_path)
        elif audio_bytes:
            content = audio_bytes
        else:
            raise ValueError("Either audio_bytes or file_path must be provided.")

        # Dynamically determine audio MIME type
        mime_type = "audio/wav"
        if filename:
            ext = os.path.splitext(filename)[1].lower()
            if ext in [".ogg", ".opus"]:
                mime_type = "audio/ogg"
            elif ext == ".mp3":
                mime_type = "audio/mpeg"
            elif ext == ".m4a":
                mime_type = "audio/mp4"
            elif ext == ".wav":
                mime_type = "audio/wav"

        files = {
            "file": (filename, content, mime_type)
        }
        data = {
            "model": stt_model
        }
        if language_code:
            data["language_code"] = language_code

        response = self.session.post(
            endpoint,
            headers=self.headers,
            files=files,
            data=data,
            timeout=60
        )

        if response.status_code != 200:
            logger.error(f"Sarvam STT Error ({response.status_code}): {response.text}")
            raise RuntimeError(f"Sarvam STT failed: {response.text}")

        res_json = response.json()
        return AudioTranscriptionResponse(
            transcript=res_json.get("transcript", "").strip(),
            language_code=res_json.get("language_code", language_code or "unknown"),
            request_id=res_json.get("request_id")
        )

    # ==========================================
    # 2. MULTILINGUAL LLM EXTRACTION (Sarvam-105b)
    # ==========================================
    def extract_financial_intent(
        self,
        transcript: str,
        language_hint: Optional[str] = None,
        model: Optional[str] = None
    ) -> VoiceAgentExtraction:
        """
        Processes multilingual voice transcript using Sarvam-105b LLM and
        extracts structured payment entities, routing decisions, and action plans.
        """
        llm_model = model or settings.SARVAM_LLM_MODEL
        endpoint = f"{self.base_url}/v1/chat/completions"

        system_prompt = """You are an expert multilingual AI router and entity extractor for a payment orchestration system (Cashfree).
Your job is to analyze the user's speech transcript and output a strictly valid JSON object.

Available Agents:
1. "payment_recovery_agent": [CRITICAL/HIGH PRIORITY] Handles failed payments, money debited but order/booking failed, expired links, payment retries, customer disputes.
2. "reconciliation_agent": Handles payment reconciliation, bank statement vs gateway status discrepancies, missing settlements, UTR checks.
3. "leakage_detection_agent": Handles MDR fee audits, double refunds, unexpected commission charges, untracked payment leaks.
4. "payout_management_agent": Handles vendor/partner payouts, bank account/UPI VPA verification, balance checks.
5. "general_support_agent": Fallback for unclassified questions.

Output must be ONLY a valid JSON object matching this exact structure:
{
  "detected_language": "<e.g., hi-IN, en-IN, ta-IN, te-IN, bn-IN, mr-IN>",
  "intent": "<RECOVER_FAILED_PAYMENT | CREATE_PAYMENT_LINK | CHECK_PAYMENT_STATUS | INITIATE_REFUND | RECONCILE_TRANSACTIONS | CHECK_SETTLEMENT | DISCREPANCY_ANALYSIS | DETECT_FEE_LEAKAGE | AUDIT_MDR_CHARGES | UNCAPTURED_PAYMENTS | INITIATE_PAYOUT | VERIFY_BENEFICIARY | CHECK_PAYOUT_BALANCE | UNKNOWN>",
  "target_agent": "<payment_recovery_agent | reconciliation_agent | leakage_detection_agent | payout_management_agent | general_support_agent>",
  "priority": "<CRITICAL | HIGH | MEDIUM | LOW>",
  "entities": {
    "transaction_id": "<string or null>",
    "order_id": "<string or null>",
    "amount": <number or null>,
    "currency": "INR",
    "customer_phone": "<string or null>",
    "customer_email": "<string or null>",
    "beneficiary_vpa": "<string or null>",
    "beneficiary_account": "<string or null>",
    "beneficiary_ifsc": "<string or null>",
    "gateway": "Cashfree"
  },
  "confidence_score": 0.95,
  "agent_action_prompt": "<Clear, concise instruction for the assigned downstream agent explaining what to verify and execute>",
  "reply_summary": "<A polite, professional acknowledgement sentence in the user's spoken language to be spoken back via TTS>"
}

Rules:
- If a customer mentions money was deducted, payment failed, or asks to recover/retry, target MUST be "payment_recovery_agent" and priority MUST be "HIGH" or "CRITICAL".
- If user speaks in Hindi or Hinglish, detected_language should be "hi-IN" and reply_summary must be in natural, polite Hindi.
- Always output raw valid JSON only. Do not add markdown backticks, conversational preamble or sign-off."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User Transcript: \"{transcript}\""}
        ]
        if language_hint:
            messages.append({"role": "system", "content": f"Note: Detected spoken language is {language_hint}"})

        payload = {
            "model": llm_model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 2048
        }

        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json"
        }

        response = self.session.post(endpoint, headers=headers, json=payload, timeout=45)
        if response.status_code != 200:
            logger.error(f"Sarvam LLM Error ({response.status_code}): {response.text}")
            raise RuntimeError(f"Sarvam LLM failed: {response.text}")

        res_json = response.json()
        choice = res_json["choices"][0]
        message_data = choice.get("message", {})
        raw_content = message_data.get("content") or message_data.get("reasoning_content") or ""
        raw_content = raw_content.strip()
        logger.info(f"Raw LLM Output:\n{raw_content}")

        # Parse JSON from content (stripping markdown fence if present)
        parsed_data = self._clean_and_parse_json(raw_content)

        # Normalize priority & agent if payment recovery
        intent_str = str(parsed_data.get("intent", "UNKNOWN")).upper()
        if "RECOVER" in intent_str or "FAILED" in intent_str or "REFUND" in intent_str:
            parsed_data["target_agent"] = AgentTarget.PAYMENT_RECOVERY.value
            if parsed_data.get("priority") not in ["HIGH", "CRITICAL"]:
                parsed_data["priority"] = "HIGH"

        # Build FinancialEntities
        entities_raw = parsed_data.get("entities", {}) or {}
        entities = FinancialEntities(
            transaction_id=entities_raw.get("transaction_id"),
            order_id=entities_raw.get("order_id"),
            amount=float(entities_raw["amount"]) if entities_raw.get("amount") is not None else None,
            currency=entities_raw.get("currency", "INR"),
            customer_phone=entities_raw.get("customer_phone"),
            customer_email=entities_raw.get("customer_email"),
            beneficiary_vpa=entities_raw.get("beneficiary_vpa"),
            beneficiary_account=entities_raw.get("beneficiary_account"),
            beneficiary_ifsc=entities_raw.get("beneficiary_ifsc"),
            gateway=entities_raw.get("gateway", "Cashfree")
        )

        target_agent_enum = self._map_target_agent(parsed_data.get("target_agent"))
        priority_enum = self._map_priority(parsed_data.get("priority"))
        intent_enum = self._map_intent(parsed_data.get("intent"))

        return VoiceAgentExtraction(
            raw_transcript=transcript,
            detected_language=parsed_data.get("detected_language", language_hint or "hi-IN"),
            intent=intent_enum,
            target_agent=target_agent_enum,
            priority=priority_enum,
            entities=entities,
            confidence_score=float(parsed_data.get("confidence_score", 0.95)),
            agent_action_prompt=parsed_data.get("agent_action_prompt", "Process financial request."),
            reply_summary=parsed_data.get("reply_summary", "आपका अनुरोध प्राप्त हो गया है, जांच की जा रही है।")
        )

    # ==========================================
    # 3. TEXT-TO-SPEECH (Bulbul)
    # ==========================================
    def generate_speech(
        self,
        text: str,
        target_language_code: str = "hi-IN",
        speaker: Optional[str] = None,
        model: Optional[str] = None
    ) -> SpeechSynthesisResponse:
        """
        Synthesizes localized audio response using Sarvam Bulbul model.
        Returns base64 encoded audio string.
        """
        tts_model = model or settings.SARVAM_TTS_MODEL
        selected_speaker = speaker or settings.SARVAM_DEFAULT_TTS_SPEAKER
        endpoint = f"{self.base_url}/text-to-speech"

        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "inputs": [text],
            "target_language_code": target_language_code,
            "speaker": selected_speaker,
            "model": tts_model
        }

        response = self.session.post(endpoint, headers=headers, json=payload, timeout=45)
        if response.status_code != 200:
            logger.error(f"Sarvam TTS Error ({response.status_code}): {response.text}")
            raise RuntimeError(f"Sarvam TTS failed: {response.text}")

        res_json = response.json()
        audios = res_json.get("audios", [])
        if not audios:
            raise RuntimeError("Sarvam TTS returned empty audio list.")

        return SpeechSynthesisResponse(
            audio_base64=audios[0],
            language_code=target_language_code,
            speaker=selected_speaker,
            model=tts_model
        )

    # ==========================================
    # 4. UNIFIED PIPELINE (Audio -> JSON -> TTS)
    # ==========================================
    def process_voice_input(
        self,
        audio_bytes: Optional[bytes] = None,
        file_path: Optional[str] = None,
        filename: str = "voice_input.wav",
        language_code: Optional[str] = None,
        generate_voice_reply: bool = True
    ) -> Dict[str, Any]:
        """
        End-to-end voice ingestion helper:
        1. Speech-to-Text via Saaras
        2. Financial Intent & Entity Extraction via Sarvam-105b
        3. Localized Acknowledgment Audio via Bulbul
        """
        # Step A: STT
        stt_result = self.transcribe_audio(
            audio_bytes=audio_bytes,
            file_path=file_path,
            filename=filename,
            language_code=language_code
        )
        logger.info(f"Transcribed Text: '{stt_result.transcript}' (Lang: {stt_result.language_code})")

        if not stt_result.transcript:
            empty_reply = "माफ़ कीजिये, आवाज़ साफ़ नहीं आई। कृपया दोबारा बोलें।"
            tts_audio_base64 = None
            if generate_voice_reply:
                try:
                    tts_result = self.generate_speech(text=empty_reply, target_language_code="hi-IN")
                    tts_audio_base64 = tts_result.audio_base64
                except Exception:
                    pass
            return {
                "transcript": "(कोई आवाज़ नहीं पहचानी गई / No speech detected)",
                "language": stt_result.language_code or "hi-IN",
                "structured_json": {
                    "raw_transcript": "",
                    "detected_language": stt_result.language_code or "hi-IN",
                    "intent": "UNKNOWN",
                    "target_agent": "general_support_agent",
                    "priority": "LOW",
                    "entities": {},
                    "confidence_score": 0.0,
                    "agent_action_prompt": "No clear speech detected. Ask user to speak closer to the microphone.",
                    "reply_summary": empty_reply
                },
                "target_agent": "general_support_agent",
                "priority": "LOW",
                "acknowledgement_text": empty_reply,
                "acknowledgement_audio_base64": tts_audio_base64
            }

        # Step B: LLM Extraction
        extraction = self.extract_financial_intent(
            transcript=stt_result.transcript,
            language_hint=stt_result.language_code
        )

        # Step C: Optional TTS Acknowledgment
        tts_audio_base64 = None
        if generate_voice_reply and extraction.reply_summary:
            try:
                tts_result = self.generate_speech(
                    text=extraction.reply_summary,
                    target_language_code=extraction.detected_language
                )
                tts_audio_base64 = tts_result.audio_base64
            except Exception as e:
                logger.warning(f"Failed to generate TTS acknowledgment: {e}")

        return {
            "transcript": stt_result.transcript,
            "language": extraction.detected_language,
            "structured_json": extraction.model_dump(),
            "target_agent": extraction.target_agent.value,
            "priority": extraction.priority.value,
            "acknowledgement_text": extraction.reply_summary,
            "acknowledgement_audio_base64": tts_audio_base64
        }

    # ==========================================
    # INTERNAL HELPERS
    # ==========================================
    def _clean_and_parse_json(self, content: str) -> Dict[str, Any]:
        """Extract and safely parse JSON from raw LLM output."""
        cleaned = re.sub(r"^```json\s*", "", content, flags=re.MULTILINE)
        cleaned = re.sub(r"^```\s*$", "", cleaned, flags=re.MULTILINE).strip()
        
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Fallback regex search for { ... }
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    pass
            logger.warning(f"Could not parse valid JSON from:\n{content}")
            return {}

    def _map_target_agent(self, val: Optional[str]) -> AgentTarget:
        try:
            return AgentTarget(val)
        except Exception:
            return AgentTarget.GENERAL_SUPPORT

    def _map_priority(self, val: Optional[str]) -> PriorityLevel:
        try:
            return PriorityLevel(str(val).upper())
        except Exception:
            return PriorityLevel.MEDIUM

    def _map_intent(self, val: Optional[str]) -> IntentCategory:
        try:
            return IntentCategory(str(val).upper())
        except Exception:
            return IntentCategory.UNKNOWN

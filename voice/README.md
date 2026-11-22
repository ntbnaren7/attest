# WhatsApp Voice & Intelligence Layer

**Core Contributor & Module Author:** DevSanjay09 ([DevSanjay09/voice-sarvam](https://github.com/DevSanjay09/voice-sarvam))

This module provides the conversational voice and text intelligence pipeline for **Attest**.
It handles customer-facing interactions: ingesting WhatsApp-style voice notes or text, extracting structured financial claims and entities, and providing multilingual responses.

---

## Key Capabilities

1. **Speech-to-Text (STT)**:
   - Multilingual transcription via Sarvam Saaras (`saaras:v3`).
   - Handles standard WhatsApp audio codecs including OGG/Opus, MP3, WAV, and M4A.

2. **Multilingual Intelligence & Entity Extraction**:
   - Primary engine: Sarvam 105B Conversations (`sarvam-105b-conversations`).
   - Extracts structured entities:
     - Order IDs (`CFORD-...`, `ORD_...`, `INV-...`, `L-...`, `B-...`)
     - Payment & transaction IDs (12-digit UPI UTRs and bank references)
     - Amounts & beneficiary VPAs
     - Intents (`RECOVER_FAILED_PAYMENT`, `CHECK_PAYMENT_STATUS`, `RECONCILE_TRANSACTIONS`, `INITIATE_PAYOUT`, `DETECT_FEE_LEAKAGE`)
   - **Resilient Offline Fallback**:
     - Fast rule-based entity and intent extraction when offline or when API keys are unconfigured.
     - Multilingual script and keyword detection (Hindi, Tamil, Kannada, Telugu, Bengali, Malayalam, Hinglish).

3. **Text-to-Speech (TTS)**:
   - Voice responses synthesized via Sarvam Bulbul (`bulbul:v3`).
   - Supports language-matched voice audio in Indian languages.

4. **Attest Integration**:
   - Wired into `app/inbox.py`. Extracted claims are verified against Cashfree gateway facts and ledger records.

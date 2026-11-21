from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class AgentTarget(str, Enum):
    PAYMENT_RECOVERY = "payment_recovery_agent"          # High priority recovery
    PAYMENT_RECONCILIATION = "reconciliation_agent"       # Gateway vs Ledger reconciliation
    REVENUE_LEAKAGE = "leakage_detection_agent"           # Fee leaks, uncaptured payments
    PAYOUT_MANAGEMENT = "payout_management_agent"         # Vendor/Partner payouts, VPA verify
    GENERAL_SUPPORT = "general_support_agent"             # General fallback

class PriorityLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class IntentCategory(str, Enum):
    # Payment Recovery
    RECOVER_FAILED_PAYMENT = "RECOVER_FAILED_PAYMENT"
    CREATE_PAYMENT_LINK = "CREATE_PAYMENT_LINK"
    CHECK_PAYMENT_STATUS = "CHECK_PAYMENT_STATUS"
    INITIATE_REFUND = "INITIATE_REFUND"
    
    # Reconciliation
    RECONCILE_TRANSACTIONS = "RECONCILE_TRANSACTIONS"
    CHECK_SETTLEMENT = "CHECK_SETTLEMENT"
    DISCREPANCY_ANALYSIS = "DISCREPANCY_ANALYSIS"
    
    # Revenue Leakage
    DETECT_FEE_LEAKAGE = "DETECT_FEE_LEAKAGE"
    AUDIT_MDR_CHARGES = "AUDIT_MDR_CHARGES"
    UNCAPTURED_PAYMENTS = "UNCAPTURED_PAYMENTS"
    
    # Payout Management
    INITIATE_PAYOUT = "INITIATE_PAYOUT"
    VERIFY_BENEFICIARY = "VERIFY_BENEFICIARY"
    CHECK_PAYOUT_BALANCE = "CHECK_PAYOUT_BALANCE"
    
    UNKNOWN = "UNKNOWN"

class FinancialEntities(BaseModel):
    transaction_id: Optional[str] = Field(None, description="Payment or transaction identifier (e.g., TXN_12345, UTR)")
    order_id: Optional[str] = Field(None, description="Merchant order ID")
    amount: Optional[float] = Field(None, description="Monetary value mentioned")
    currency: str = Field("INR", description="Currency code (default INR)")
    customer_phone: Optional[str] = Field(None, description="Phone number of the customer")
    customer_email: Optional[str] = Field(None, description="Email address")
    beneficiary_vpa: Optional[str] = Field(None, description="UPI ID / VPA for payouts or verification")
    beneficiary_account: Optional[str] = Field(None, description="Bank account number")
    beneficiary_ifsc: Optional[str] = Field(None, description="Bank IFSC code")
    gateway: Optional[str] = Field("Cashfree", description="Payment gateway reference")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional custom key-value attributes")

class VoiceAgentExtraction(BaseModel):
    raw_transcript: str = Field(..., description="Original transcribed voice text from Sarvam Saaras")
    detected_language: str = Field("hi-IN", description="Language code of input (e.g., hi-IN, en-IN, ta-IN, te-IN)")
    intent: IntentCategory = Field(..., description="Primary classification of user intent")
    target_agent: AgentTarget = Field(..., description="The designated agent to route this request to")
    priority: PriorityLevel = Field(..., description="Execution priority (Recovery requests default to HIGH/CRITICAL)")
    entities: FinancialEntities = Field(default_factory=FinancialEntities, description="Extracted payment entities")
    confidence_score: float = Field(default=0.95, description="Confidence in classification (0.0 to 1.0)")
    agent_action_prompt: str = Field(..., description="Concrete, actionable prompt for the downstream LangGraph agent")
    reply_summary: str = Field(..., description="Natural language acknowledgement in the user's native language for Sarvam Bulbul TTS")

class AudioTranscriptionResponse(BaseModel):
    transcript: str
    language_code: Optional[str] = None
    request_id: Optional[str] = None

class SpeechSynthesisResponse(BaseModel):
    audio_base64: str
    language_code: str
    speaker: str
    model: str

class WhatsAppTextRequest(BaseModel):
    text: str = Field(..., description="Incoming customer text message from WhatsApp")
    phone_number: Optional[str] = Field(None, description="Sender's WhatsApp phone number (e.g. +919876543210)")
    language_code: Optional[str] = Field(None, description="Optional language hint (e.g. en-IN, hi-IN)")
    run_cashfree_agents: Optional[bool] = Field(False, description="Whether to also run the internal 4-agent LangGraph swarm")

class CashfreeActionPayload(BaseModel):
    target_action: Optional[str] = Field(None, description="payment_recovery, reconciliation, leakage_detection, payout_management, customer_support")
    priority: Optional[str] = Field("MEDIUM", description="CRITICAL, HIGH, MEDIUM, LOW")
    intent: Optional[str] = Field("UNKNOWN", description="Underlying detected intent")
    entities: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Extracted amounts, order_id, txn_id, VPA, phone, etc.")
    action_prompt: Optional[str] = Field(None, description="Detailed instruction for Cashfree agent/API relay")
    confidence_score: Optional[float] = Field(0.95, description="Confidence score")

class WhatsAppProcessResponse(BaseModel):
    success: bool
    input_type: str = Field(..., description="'voice' or 'text'")
    sender_phone: Optional[str] = None
    transcript: str = Field(..., description="Transcribed audio or original text")
    detected_language: str
    reply_mode: str = Field(..., description="'voice' or 'text'")
    reply_text: str = Field(..., description="Natural, conversational, question-specific response")
    reply_audio_base64: Optional[str] = Field(None, description="Base64 encoded WAV audio (present if input was voice)")
    reply_audio_mime: Optional[str] = Field(None, description="'audio/wav' if voice, None if text")
    cashfree_action: Optional[CashfreeActionPayload] = None
    cashfree_agent_result: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    error: Optional[str] = None

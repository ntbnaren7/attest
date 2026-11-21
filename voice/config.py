import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    SARVAM_API_KEY: str = ""  # passed in explicitly by app/inbox.py (never commit a key here)
    SARVAM_BASE_URL: str = "https://api.sarvam.ai"
    SARVAM_STT_MODEL: str = "saaras:v3"
    SARVAM_LLM_MODEL: str = "sarvam-105b-conversations"
    SARVAM_TTS_MODEL: str = "bulbul:v3"
    SARVAM_DEFAULT_TTS_SPEAKER: str = "priya"
    
    # Cashfree Gateway
    CASHFREE_APP_ID: str = ""
    CASHFREE_SECRET_KEY: str = ""
    CASHFREE_ENV: str = "TEST"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

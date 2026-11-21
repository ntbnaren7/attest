"""Sarvam model APIs (docs fetched 2026-11-21).

  STT  POST /speech-to-text   multipart: file, model=saaras:v4, mode, language_code -> {transcript, language_code}
  Chat POST /v1/chat/completions  model=sarvam-105b, OpenAI-style tools/tool_choice
  TTS  POST /text-to-speech   {text, language_code, model, speaker} -> {audios: [base64 wav]}

The model only extracts, asks and explains. It never decides eligibility or authorizes anything.
"""
from __future__ import annotations

import json
import os

import httpx


class SarvamError(Exception):
    pass


class SarvamLive:
    provenance = "sarvam_live"

    def __init__(self, api_key: str, base_url: str, chat_model: str, stt_model: str, tts_model: str, speaker: str):
        self.base = base_url.rstrip("/")
        self.chat_model, self.stt_model, self.tts_model, self.speaker = chat_model, stt_model, tts_model, speaker
        self._key = api_key
        self._client = httpx.Client(timeout=60)

    def _headers(self, json_ct=True):
        h = {"api-subscription-key": self._key, "Authorization": f"Bearer {self._key}"}
        if json_ct:
            h["Content-Type"] = "application/json"
        return h

    def stt(self, audio: bytes, filename: str, content_type: str, language_code: str = "unknown",
            mode: str = "codemix") -> dict:
        files = {"file": (filename, audio, content_type or "application/octet-stream")}
        data = {"model": self.stt_model, "language_code": language_code or "unknown", "mode": mode}
        r = self._client.post(f"{self.base}/speech-to-text", headers=self._headers(json_ct=False), files=files, data=data)
        if r.status_code >= 400:
            raise SarvamError(f"STT HTTP {r.status_code}: {r.text[:300]}")
        j = r.json()
        return {"transcript": j.get("transcript", ""), "language_code": j.get("language_code"),
                "request_id": j.get("request_id"), "model": self.stt_model}

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice=None) -> dict:
        body = {"model": self.chat_model, "messages": messages, "temperature": 0.2, "max_tokens": 1024,
                "reasoning_effort": os.environ.get("SARVAM_REASONING_EFFORT", "low") or None}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "required"
        r = self._client.post(f"{self.base}/v1/chat/completions", headers=self._headers(), json=body)
        if r.status_code >= 400:
            raise SarvamError(f"chat HTTP {r.status_code}: {r.text[:300]}")
        j = r.json()
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        return {"message": msg, "model": j.get("model"), "id": j.get("id"), "usage": j.get("usage")}

    def tts(self, text: str, language_code: str) -> dict:
        body = {"text": text[:2500], "language_code": language_code, "model": self.tts_model, "speaker": self.speaker}
        r = self._client.post(f"{self.base}/text-to-speech", headers=self._headers(), json=body)
        if r.status_code >= 400:
            raise SarvamError(f"TTS HTTP {r.status_code}: {r.text[:300]}")
        j = r.json()
        audios = j.get("audios") or []
        return {"audio_b64": audios[0] if audios else None, "request_id": j.get("request_id"), "model": self.tts_model}


def parse_tool_call(message: dict) -> tuple[str | None, dict | None]:
    """Return (tool_name, arguments) from a chat message; accepts tool_calls or JSON content."""
    calls = message.get("tool_calls") or []
    if calls:
        fn = calls[0].get("function") or {}
        try:
            return fn.get("name"), json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            return fn.get("name"), None
    content = (message.get("content") or "").strip()
    if content.startswith("```"):
        content = content.strip("`").split("\n", 1)[-1]
    try:
        j = json.loads(content)
        if isinstance(j, dict):
            return j.get("tool") or "route_operator_request", j.get("arguments", j)
    except json.JSONDecodeError:
        pass
    return None, None

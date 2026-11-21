"""Sarvam model APIs (docs fetched 2026-11-21).

  STT  POST /speech-to-text   multipart: file, model=saaras:v4, mode, language_code -> {transcript, language_code}
  Chat POST /v1/chat/completions  model=sarvam-105b, OpenAI-style tools/tool_choice
  TTS  POST /text-to-speech   {text, language_code, model, speaker} -> {audios: [base64 wav]}

The model only extracts, asks and explains. It never decides eligibility or authorizes anything.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict

import httpx


class SarvamError(Exception):
    pass


class SarvamLive:
    provenance = "sarvam_live"

    def __init__(self, api_key: str, base_url: str, chat_model: str, stt_model: str, tts_model: str, speaker: str,
                 fallback_keys: tuple[str, ...] = ()):
        self.base = base_url.rstrip("/")
        self.chat_model, self.stt_model, self.tts_model, self.speaker = chat_model, stt_model, tts_model, speaker
        self._key = api_key
        # Extra keys are tried in order when the current one is rate-limited or out of quota.
        self._keys = [api_key] + [k for k in fallback_keys if k and k != api_key]
        self._client = httpx.Client(timeout=60)
        self._tts_cache: OrderedDict[tuple[str, str], dict] = OrderedDict()

    def _post(self, path: str, json_ct: bool = True, **kw) -> httpx.Response:
        """POST with key failover: on 429 or a quota/credit refusal, retry once per spare key."""
        r = None
        for i, key in enumerate(self._keys):
            r = self._client.post(f"{self.base}{path}", headers=self._headers(json_ct, key), **kw)
            limited = r.status_code == 429 or (r.status_code in (402, 403) and any(
                w in r.text.lower() for w in ("quota", "credit", "limit", "insufficient")))
            if not limited or i == len(self._keys) - 1:
                return r
        return r

    def _headers(self, json_ct=True, key: str | None = None):
        key = key or self._key
        h = {"api-subscription-key": key, "Authorization": f"Bearer {key}"}
        if json_ct:
            h["Content-Type"] = "application/json"
        return h

    def stt(self, audio: bytes, filename: str, content_type: str, language_code: str = "unknown",
            mode: str = "codemix") -> dict:
        files = {"file": (filename, audio, content_type or "application/octet-stream")}
        data = {"model": self.stt_model, "language_code": language_code or "unknown", "mode": mode}
        r = self._post("/speech-to-text", json_ct=False, files=files, data=data)
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
        r = self._post("/v1/chat/completions", json=body)
        if r.status_code >= 400:
            raise SarvamError(f"chat HTTP {r.status_code}: {r.text[:300]}")
        j = r.json()
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        return {"message": msg, "model": j.get("model"), "id": j.get("id"), "usage": j.get("usage")}

    def tts_cached(self, text: str, language_code: str) -> bool:
        return (text[:2500], language_code) in self._tts_cache

    def tts(self, text: str, language_code: str) -> dict:
        key = (text[:2500], language_code)
        hit = self._tts_cache.get(key)
        if hit:
            self._tts_cache.move_to_end(key)
            return {**hit, "cached": True}
        out = self._tts_uncached(text, language_code)
        if out.get("audio_b64"):
            self._tts_cache[key] = out
            while len(self._tts_cache) > 300:  # bounded memory; most-recent kept
                self._tts_cache.popitem(last=False)
        return out

    def _tts_uncached(self, text: str, language_code: str) -> dict:
        body = {"text": text[:2500], "language_code": language_code, "model": self.tts_model, "speaker": self.speaker}
        r = self._post("/text-to-speech", json=body)
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

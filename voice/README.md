# WhatsApp voice & intelligence layer (teammate's code, vendored)

Copied from https://github.com/DevSanjay09/voice-sarvam at commit `3eea85a`: `config.py`, `schemas.py` and
`services/`. The only change: the hard-coded Sarvam key default in `config.py` is blanked. The key comes from
`SARVAM_VOICE_API_KEY` in `.env`, and `app/inbox.py` passes it in.

It handles the customer side: a WhatsApp-style voice note or text goes through Sarvam speech-to-text, the
`sarvam-105b-conversations` model (entities, intent, natural reply) and Bulbul text-to-speech. `app/inbox.py` turns the
result into claims on the right account, runs the payment check, and only lets the natural reply through when it
makes no promise or amount the verified facts do not support.

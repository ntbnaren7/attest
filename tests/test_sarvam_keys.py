"""Key failover: a rate-limited key hands the call to the spare key."""
import httpx

from app.providers.sarvam import SarvamLive


def test_rate_limited_key_fails_over_to_spare():
    seen = []

    def handler(req: httpx.Request):
        key = req.headers["api-subscription-key"]
        seen.append(key)
        if key == "first":
            return httpx.Response(429, json={"error": "rate limit"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "model": "m"})

    s = SarvamLive("first", "https://x", "m", "s", "t", "sp", fallback_keys=("second",))
    s._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert s.chat([{"role": "user", "content": "hi"}])["message"]["content"] == "ok"
    assert seen == ["first", "second"]


def test_other_errors_do_not_burn_the_spare_key():
    seen = []

    def handler(req):
        seen.append(req.headers["api-subscription-key"])
        return httpx.Response(400, json={"error": "bad request"})

    s = SarvamLive("first", "https://x", "m", "s", "t", "sp", fallback_keys=("second",))
    s._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        s.chat([{"role": "user", "content": "hi"}])
    except Exception:
        pass
    assert seen == ["first"]

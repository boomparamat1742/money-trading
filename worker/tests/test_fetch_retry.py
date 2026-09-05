"""fetch_binance ต้องทน 429/418 (ถอยแล้วลองใหม่) — ไม่งั้น worker พังตอน IP ร่วมโดน rate limit"""
import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from backtest import fetch_binance


def _http_error(code, retry_after=None):
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("http://x", code, "err", hdrs, io.BytesIO(b"body"))


class _OK:
    def __init__(self, payload): self._p = payload
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self._p).encode()


def test_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429)                       # โดน rate limit ครั้งแรก
        return _OK([[1000, "1", "2", "0", "1.5", "10"]])  # ครั้งที่สองสำเร็จ

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(fetch_binance.time, "sleep", lambda s: None)   # ไม่รอจริงในเทสต์
    rows = fetch_binance.fetch("BTCUSDT", "1h", 1)
    assert calls["n"] == 2          # ลองใหม่หลัง 429 (ไม่ crash)
    assert len(rows) == 1


def test_honors_retry_after_header(monkeypatch):
    waited = []

    def fake(req, timeout=None):
        if not waited:                                   # ครั้งแรก 429 + Retry-After
            raise _http_error(429, retry_after=7)
        return _OK([[1000, "1", "2", "0", "1.5", "10"]])

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(fetch_binance.time, "sleep", lambda s: waited.append(s))
    fetch_binance.fetch("BTCUSDT", "1h", 1)
    assert 7 in waited              # เคารพ Retry-After จาก Binance


def test_non_rate_limit_error_fails_fast(monkeypatch):
    def fake(req, timeout=None):
        raise _http_error(400)                           # bad request → ไม่ retry

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(fetch_binance.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit):
        fetch_binance.fetch("BTCUSDT", "1h", 1)

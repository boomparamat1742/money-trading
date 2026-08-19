import asyncio

from worker.app.models import Direction, Signal
from worker.app.notifier import (DailyQuota, DiscordNotifier, Notifier, fmt_price,
                                 format_signal, suggest_leverage)


class _Fake(Notifier):
    def __init__(self):
        self.sent = []

    async def send(self, text: str, priority: str = "normal") -> bool:
        self.sent.append(text)
        return True


def test_daily_quota_caps_messages():
    inner = _Fake()
    q = DailyQuota(inner, max_per_day=3)
    results = [asyncio.run(q.send(f"m{i}")) for i in range(5)]
    assert results == [True, True, True, False, False]
    assert inner.sent == ["m0", "m1", "m2"]      # เกินเพดานถูกงด


def test_daily_quota_resets_next_day(monkeypatch):
    inner = _Fake()
    q = DailyQuota(inner, max_per_day=1)
    assert asyncio.run(q.send("a")) is True
    assert asyncio.run(q.send("b")) is False
    q._day -= 1                           # simulate the day rolling over
    assert asyncio.run(q.send("c")) is True
    assert len(inner.sent) == 2


class _WithQuota(_Fake):
    """notifier ที่รายงานโควตาได้ เหมือน LineNotifier.quota()"""

    def __init__(self, remaining, limit=500):
        super().__init__()
        self.q = {"limit": limit, "used": limit - remaining, "remaining": remaining}
        self.quota_calls = 0

    async def quota(self):
        self.quota_calls += 1
        return self.q


def test_monthly_quota_stops_sending_before_line_cuts_us_off():
    """เหลือน้อยกว่า reserve = หยุดเอง ไม่ปล่อยให้ LINE ตัดกลางทางแบบเงียบๆ"""
    inner = _WithQuota(remaining=5)
    q = DailyQuota(inner, max_per_day=100, monthly_reserve=20)
    assert asyncio.run(q.send("a")) is False
    assert inner.sent == []


def test_daily_cap_still_applies_when_monthly_quota_is_healthy():
    inner = _WithQuota(remaining=400)
    q = DailyQuota(inner, max_per_day=2, monthly_reserve=20)
    assert [asyncio.run(q.send(f"m{i}")) for i in range(4)] == [True, True, False, False]
    assert len(inner.sent) == 2


def test_high_priority_bypasses_daily_cap_but_normal_does_not():
    """SL/ปิด/สรุป (high) ต้องได้เสมอ แม้สัญญาณเปิดชนเพดานรายวันแล้ว"""
    inner = _WithQuota(remaining=400)
    q = DailyQuota(inner, max_per_day=2)
    asyncio.run(q.send("open1"))            # normal
    asyncio.run(q.send("open2"))            # normal → ครบเพดาน 2
    assert asyncio.run(q.send("open3")) is False              # normal ถูกงด
    assert asyncio.run(q.send("ปิด #5 SL", priority="high")) is True  # high ยังส่งได้
    assert "ปิด #5 SL" in inner.sent


def test_low_floor_reserves_quota_for_high_priority():
    """เหลือระหว่าง low_floor..reserve → งดสัญญาณเปิด แต่ยังส่ง SL/ปิด"""
    inner = _WithQuota(remaining=30)
    q = DailyQuota(inner, max_per_day=100, monthly_reserve=15, low_floor=50)
    assert asyncio.run(q.send("open", priority="normal")) is False    # 30 ≤ 50 → งด
    assert asyncio.run(q.send("SL close", priority="high")) is True   # high ยังผ่าน
    assert inner.sent == ["SL close"]


def test_suppressed_count_tracked_for_summary():
    inner = _WithQuota(remaining=30)
    q = DailyQuota(inner, max_per_day=100, low_floor=50)
    asyncio.run(q.send("open1"))            # งด
    asyncio.run(q.send("open2"))            # งด
    asyncio.run(q.send("SL", priority="high"))   # ส่ง
    assert q.pop_suppressed() == 2
    assert q.pop_suppressed() == 0          # reset หลังอ่าน


def test_quota_is_re_read_periodically_not_every_message():
    """ถามทุกข้อความ = 2 HTTP call ต่อ 1 การแจ้งเตือน สิ้นเปลืองเปล่า"""
    inner = _WithQuota(remaining=400)
    q = DailyQuota(inner, max_per_day=100, refresh_every=3)
    for _ in range(7):
        asyncio.run(q.send("x"))
    assert inner.quota_calls == 3       # ครั้งแรก + ทุก 3 ข้อความ


def test_unlimited_plan_never_blocks():
    class _Unlimited(_Fake):
        async def quota(self):
            return None                 # type != "limited"

    inner = _Unlimited()
    q = DailyQuota(inner, max_per_day=100)
    assert asyncio.run(q.send("a")) is True


def test_quota_lookup_failure_does_not_silence_alerts():
    """อ่านโควตาไม่ได้ต้องส่งต่อ — เงียบเพราะ API ล่มคือแย่ที่สุด"""
    class _Broken(_Fake):
        async def quota(self):
            raise RuntimeError("HTTP 500")

    inner = _Broken()
    q = DailyQuota(inner, max_per_day=100)
    assert asyncio.run(q.send("a")) is True
    assert inner.sent == ["a"]


def test_notifier_without_quota_support_is_unaffected():
    q = DailyQuota(_Fake(), max_per_day=2)          # ConsoleNotifier ไม่มี .quota
    assert asyncio.run(q.send("a")) is True


def test_leverage_capped_and_never_below_one():
    # very tight stop → math allows huge leverage, but the cap must bind
    lev = suggest_leverage(entry=100.0, stop=99.9)
    assert lev["leverage"] <= lev["cap"]
    # very wide stop → suggestion must still be a usable >= 1
    wide = suggest_leverage(entry=100.0, stop=40.0)
    assert wide["leverage"] >= 1


def test_leverage_none_without_prices():
    assert suggest_leverage(None, 97.0) is None
    assert suggest_leverage(100.0, 100.0) is None  # zero stop distance


def test_prices_show_two_decimals():
    assert fmt_price(1918.43417632) == "1,918.43"
    assert fmt_price(1926.10164736) == "1,926.10"
    assert fmt_price(None) == "-"


def test_sub_dollar_coins_keep_enough_precision_to_be_usable():
    """DOGE ที่ 0.084321 ถ้าปัดเหลือ 2 ตำแหน่งได้ 0.08 — SL/TP จะไร้ความหมาย"""
    assert fmt_price(0.084321) == "0.084321"
    assert fmt_price(0.00001234) == "1.234e-05"


def _signal(entry, sl, tp):
    return Signal(
        exchange="binance", symbol="ETHUSDT", timeframe="15m",
        candle_open_time=1_700_000_000_000, strategy_name="trend_following",
        strategy_version="1.1.0", direction=Direction.LONG, signal_score=86.03,
        score_breakdown={}, market_regime={"regime": "uptrend"},
        entry_price=entry, stop_loss=sl, take_profit=tp, expected_rr=2.0,
        risk_status="approved", rejection_reason=None, indicators={},
        trigger_reasons=["htf_aligned"], status="approved",
        position_size=0.0195632, risk_amount=0.05, risk_pct=0.5)


def test_alert_warns_when_regime_unreliable():
    """แจ้งซื่อสัตย์: sideway/ผันผวน → เตือนว่าเชื่อได้น้อย · เทรนด์ชัด → ไม่เตือน"""
    sig = _signal(100.0, 98.0, 106.0)
    sig.market_regime = {"regime": "sideway"}
    assert "เชื่อได้น้อย" in format_signal(sig)
    sig.market_regime = {"regime": "high_volatility"}
    assert "เชื่อได้น้อย" in format_signal(sig)
    sig.market_regime = {"regime": "uptrend"}
    assert "เชื่อได้น้อย" not in format_signal(sig)


def test_alert_does_not_print_raw_float_tails():
    msg = format_signal(_signal(1920.99, 1918.43417632, 1926.10164736))
    assert "ระดับ SL (อ้างอิง): 1,918.43" in msg
    assert "ระดับ TP (อ้างอิง): 1,926.10" in msg
    assert "1918.43417632" not in msg


class _FakeResp:
    def __init__(self, status, body=None):
        self.status, self._body = status, body or {}

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._body
    async def text(self): return str(self._body)


class _FakeSession:
    def __init__(self, resp): self._resp = resp

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def post(self, *a, **k): return self._resp


def test_discord_treats_204_as_success(monkeypatch):
    """webhook สำเร็จคืน 204 (ไม่ใช่ 200) — ถ้าเช็คแค่ 200 จะเข้าใจผิดว่าล้มเหลว"""
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(_FakeResp(204)))
    assert asyncio.run(DiscordNotifier("https://discord.com/api/webhooks/1/x").send("hi")) is True


def test_discord_gives_up_on_bad_webhook(monkeypatch):
    """404 = webhook ถูกลบ/ผิด — ต้องเลิก ไม่วนลองใหม่ให้เสียเวลา"""
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(_FakeResp(404, "not found")))
    assert asyncio.run(DiscordNotifier("https://discord.com/api/webhooks/1/x").send("hi")) is False


def test_build_notifier_prefers_discord_when_webhook_set(monkeypatch):
    """ตั้ง DISCORD_WEBHOOK_URL ต้องสลับมา Discord แม้ LINE creds ยังอยู่"""
    from worker.app.main import build_notifier
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")

    class S:
        line_channel_token, line_to = "tok", "u"
    assert isinstance(build_notifier(S()), DiscordNotifier)


def test_open_alert_shows_trade_ref_to_match_the_close():
    """ข้อความเปิดต้องมี "ไม้ #N" เดียวกับตอนปิด ไม่งั้นจับคู่ไม่ได้ (งง)"""
    with_ref = format_signal(_signal(100.0, 98.0, 106.0), ref=42)
    assert "ไม้ #42" in with_ref
    no_ref = format_signal(_signal(100.0, 98.0, 106.0))
    assert "ไม้ #" not in no_ref              # ไม่มี ref (เช่น console) ก็ไม่พัง

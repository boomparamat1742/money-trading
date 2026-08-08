import asyncio

from worker.app.models import Direction, Signal
from worker.app.notifier import (DailyQuota, Notifier, fmt_price, format_signal,
                                 suggest_leverage)


class _Fake(Notifier):
    def __init__(self):
        self.sent = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


def test_daily_quota_caps_messages():
    inner = _Fake()
    q = DailyQuota(inner, max_per_day=3)
    results = [asyncio.run(q.send(f"m{i}")) for i in range(5)]
    assert results == [True, True, True, False, False]
    assert len(inner.sent) == 3          # only 3 actually delivered
    assert "โควตา" in inner.sent[-1]      # last delivered message warns about the cap


def test_daily_quota_resets_next_day(monkeypatch):
    inner = _Fake()
    q = DailyQuota(inner, max_per_day=1)
    assert asyncio.run(q.send("a")) is True
    assert asyncio.run(q.send("b")) is False
    q._day -= 1                           # simulate the day rolling over
    assert asyncio.run(q.send("c")) is True
    assert len(inner.sent) == 2


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


def test_alert_does_not_print_raw_float_tails():
    msg = format_signal(_signal(1920.99, 1918.43417632, 1926.10164736))
    assert "ระดับ SL (อ้างอิง): 1,918.43" in msg
    assert "ระดับ TP (อ้างอิง): 1,926.10" in msg
    assert "1918.43417632" not in msg

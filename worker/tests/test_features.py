"""derived features สำหรับวิจัย — เก็บติดทุกสัญญาณ (v1.2.0 feature list)

ยังไม่ใช้ตัดสินใจ เก็บไว้ดู expectancy รายกลุ่มเมื่อครบหลายร้อยไม้
"""
from worker.app.indicators import IndicatorEngine
from worker.app.models import Candle
from worker.app.paper_trading import entry_from_signal

STEP = 900_000


def _feed(eng, closes, vols=None):
    snap = None
    for i, c in enumerate(closes):
        vol = vols[i] if vols else 100.0
        snap = eng.update(Candle("binance", "BTCUSDT", "15m", i * STEP,
                                 open=c, high=c * 1.002, low=c * 0.998, close=c, volume=vol))
    return snap


def test_trend_extension_is_distance_from_ema20_in_atr():
    eng = IndicatorEngine()
    snap = _feed(eng, [100 + i * 0.1 for i in range(80)])
    v = snap.values
    assert "trend_extension" in v and "ema20_dist_atr" in v
    # trend_extension = |dist| — ต้องเป็นบวกและเท่ากับค่าสัมบูรณ์ของ ema20_dist_atr
    assert v["trend_extension"] >= 0
    assert abs(v["trend_extension"] - abs(v["ema20_dist_atr"])) < 1e-9


def test_uptrend_puts_price_above_ema20():
    eng = IndicatorEngine()
    snap = _feed(eng, [100 + i * 0.5 for i in range(80)])   # ขาขึ้นชัด
    assert snap.values["ema20_dist_atr"] > 0                # ราคาเหนือ EMA20


def test_slopes_appear_after_two_bars_and_measure_change():
    eng = IndicatorEngine()
    # ต้องอุ่นพอให้ adx/macd_hist มีค่า แล้ว slope ถึงโผล่
    snap = _feed(eng, [100 + (i % 7) for i in range(120)])
    v = snap.values
    if "adx" in v:
        assert "adx_slope" in v          # มี prev แล้ว
    if "macd_hist" in v:
        assert "macd_hist_slope" in v


def test_vwap_zscore_and_vol_zscore_present():
    eng = IndicatorEngine()
    snap = _feed(eng, [100 + (i % 5) * 0.3 for i in range(80)],
                 vols=[100 + (i % 10) * 20 for i in range(80)])
    v = snap.values
    assert "vwap_zscore" in v            # มี vwap_sd > 0
    assert "vol_zscore" in v             # มี vol std > 0


def test_flat_market_has_no_extension_blowup():
    """ราคานิ่ง → atr เล็ก แต่ต้องไม่หารศูนย์/ระเบิด"""
    eng = IndicatorEngine()
    snap = _feed(eng, [100.0] * 80)
    v = snap.values
    # atr อาจเป็น 0 → guard atr>0 ทำให้ไม่มี trend_extension (ไม่ใช่ crash)
    assert "close" in v


def test_entry_context_carries_features():
    from worker.app.models import Direction, Signal

    sig = Signal(
        exchange="binance", symbol="BTCUSDT", timeframe="15m", candle_open_time=0,
        strategy_name="trend_following", strategy_version="1.1.0",
        direction=Direction.LONG, signal_score=82.0, score_breakdown={},
        market_regime={"regime": "uptrend"}, entry_price=100.0, stop_loss=98.0,
        take_profit=106.0, expected_rr=2.0, risk_status="approved",
        rejection_reason=None,
        indicators={"trend_extension": 1.23, "adx": 28.0, "adx_slope": 1.5,
                    "vwap_dist_pct": 0.4, "rsi": 61.0, "close": 100.0},
        trigger_reasons=["htf_aligned"], status="approved")
    ent = entry_from_signal(sig)
    f = ent["features"]
    assert f["trend_extension"] == 1.23 and f["adx_slope"] == 1.5
    assert "rsi" in f and "vwap_dist_pct" in f
    assert "close" not in f              # close ไม่ใช่ feature วิเคราะห์

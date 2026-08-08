"""Session VWAP — สูตรตายตัว ตรวจกับค่าที่คำนวณมือได้

VWAP = Σ(typical × volume) / Σ(volume),  typical = (H+L+C)/3
รีเซ็ตทุกวัน UTC (คริปโตไม่มี session ธรรมชาติ — ใช้แบบเดียวกับ TradingView)
"""
from worker.app.indicators import (SESSION_MS, VWAP_MIN_BARS, IndicatorEngine,
                                   session_vwap)
from worker.app.models import Candle
from worker.app.notifier import format_vwap

DAY0 = 1_700_000_000_000 // SESSION_MS * SESSION_MS   # ต้นวัน UTC พอดี
STEP = 900_000                                        # 15m


def _c(i, h, l, c, vol, day_offset=0):
    return Candle("binance", "BTCUSDT", "15m",
                  DAY0 + day_offset * SESSION_MS + i * STEP,
                  open=c, high=h, low=l, close=c, volume=vol)


def test_vwap_matches_hand_calculation():
    # 2 แท่ง: typical 100 (vol 10) และ 200 (vol 30)
    # VWAP = (100*10 + 200*30) / (10+30) = 7000/40 = 175
    candles = [_c(0, 100, 100, 100, 10), _c(1, 200, 200, 200, 30)]
    r = session_vwap(candles, min_bars=2)
    assert abs(r["vwap"] - 175.0) < 1e-9
    assert r["bars"] == 2


def test_vwap_is_volume_weighted_not_simple_average():
    """ถ้าเป็นค่าเฉลี่ยธรรมดาจะได้ 150 — VWAP ต้องเอียงไปทางแท่งที่ volume เยอะ"""
    candles = [_c(0, 100, 100, 100, 1), _c(1, 200, 200, 200, 99)]
    r = session_vwap(candles, min_bars=2)
    assert r["vwap"] > 190          # เอียงไปทาง 200 อย่างชัดเจน
    assert abs(r["vwap"] - 150) > 40


def test_returns_none_before_min_bars():
    """ต้นวันข้อมูลน้อยเกินไป VWAP ไม่มีความหมาย — ต้องไม่รายงาน"""
    candles = [_c(i, 100, 100, 100, 10) for i in range(VWAP_MIN_BARS - 1)]
    assert session_vwap(candles) is None


def test_resets_at_utc_day_boundary():
    """แท่งของเมื่อวานต้องไม่ถูกนับรวมในวันนี้"""
    yesterday = [_c(i, 100, 100, 100, 100, day_offset=-1) for i in range(20)]
    today = [_c(i, 500, 500, 500, 10, day_offset=0) for i in range(VWAP_MIN_BARS)]
    r = session_vwap(yesterday + today)
    assert abs(r["vwap"] - 500.0) < 1e-9      # เห็นเฉพาะวันนี้
    assert r["bars"] == VWAP_MIN_BARS


def test_bands_are_ordered_and_zero_when_flat():
    flat = [_c(i, 100, 100, 100, 10) for i in range(VWAP_MIN_BARS)]
    r = session_vwap(flat)
    assert r["sd"] == 0.0                      # ราคาคงที่ → เบี่ยงเบนศูนย์
    moving = [_c(i, 100 + i * 5, 100 + i * 5, 100 + i * 5, 10) for i in range(VWAP_MIN_BARS)]
    r2 = session_vwap(moving)
    assert r2["sd"] > 0


def test_engine_exposes_vwap_in_snapshot():
    eng = IndicatorEngine()
    snap = None
    for i in range(VWAP_MIN_BARS + 2):
        snap = eng.update(_c(i, 101, 99, 100, 10))
    assert "vwap" in snap.values
    v = snap.values
    assert v["vwap_lower2"] < v["vwap_lower1"] <= v["vwap"] <= v["vwap_upper1"] < v["vwap_upper2"] \
        or v["vwap_sd"] == 0.0


def test_alert_line_reports_side_and_zone():
    ind = {"close": 105.0, "vwap": 100.0, "vwap_dist_pct": 5.0,
           "vwap_upper1": 102.0, "vwap_lower1": 98.0,
           "vwap_upper2": 104.0, "vwap_lower2": 96.0}
    line = format_vwap(ind)
    assert "เหนือ" in line and "5.00%" in line and "+2σ" in line
    assert "ยังไม่ใช้ตัดสินใจ" in line      # ต้องบอกชัดว่าเป็นข้อมูลประกอบ


def test_alert_line_absent_without_vwap():
    assert format_vwap({"close": 100.0}) is None

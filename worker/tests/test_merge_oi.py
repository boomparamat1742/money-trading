"""aggregate_daily — ย่อ OI สดรายวันเป็น OHLC ถูกต้อง (open/close ตามเวลา, high/low)"""
from scripts.merge_live_oi import DAY_MS, aggregate_daily

D0 = 1_700_000_000_000 // DAY_MS * DAY_MS


def test_ohlc_open_is_first_close_is_last_by_time():
    rows = [
        ("BTCUSDT", D0 + 1000, 100.0),   # แรกของวัน
        ("BTCUSDT", D0 + 5000, 120.0),
        ("BTCUSDT", D0 + 9000, 90.0),    # สุดท้ายของวัน
    ]
    out = aggregate_daily(rows)
    o, h, l, c = out[("BTCUSDT", D0)]
    assert o == 100.0 and c == 90.0     # open=แรก close=สุดท้าย (ตามเวลา)
    assert h == 120.0 and l == 90.0


def test_unsorted_input_still_correct():
    rows = [("X", D0 + 9000, 90.0), ("X", D0 + 1000, 100.0), ("X", D0 + 5000, 120.0)]
    o, h, l, c = aggregate_daily(rows)[("X", D0)]
    assert o == 100.0 and c == 90.0     # เรียงเองก่อน


def test_splits_by_utc_day_and_symbol():
    rows = [
        ("BTC", D0 + 1000, 10.0),
        ("BTC", D0 + DAY_MS + 1000, 20.0),   # วันถัดไป
        ("ETH", D0 + 1000, 5.0),             # คนละเหรียญ
    ]
    out = aggregate_daily(rows)
    assert len(out) == 3
    assert out[("BTC", D0)][0] == 10.0
    assert out[("BTC", D0 + DAY_MS)][0] == 20.0


def test_none_values_skipped():
    rows = [("X", D0, None), ("X", D0 + 1000, 50.0)]
    out = aggregate_daily(rows)
    assert out[("X", D0)] == (50.0, 50.0, 50.0, 50.0)


def test_empty_input():
    assert aggregate_daily([]) == {}

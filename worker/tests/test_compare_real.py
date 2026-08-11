"""parse Binance Futures CSV → ประกอบ fill เป็นไม้ (รอบเข้า-ออก)

จุดเสี่ยงคือการประกอบ fill หลายอันเป็น position เดียว + แยก LONG/SHORT ให้ถูก
"""
import os
import tempfile

from scripts.compare_real_vs_paper import RealTrade, match, parse_binance_csv

HEADER = ("Uid,Time,Symbol,Side,Price,Quantity,Amount,Fee,Realized Profit,"
          "Buyer,Maker,Trade ID,Order ID\n")


def _csv(rows):
    p = os.path.join(tempfile.mkdtemp(), "trades.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write(HEADER)
        for r in rows:
            f.write(r + "\n")
    return p


def test_long_round_trip():
    # BUY แล้ว SELL ปิด → 1 ไม้ LONG กำไร
    p = _csv([
        "1,2026-08-08 10:00:00,BTCUSDT,BUY,100,1,100,0.05USDT,0,true,false,1,1",
        "1,2026-08-08 11:00:00,BTCUSDT,SELL,110,1,110,0.055USDT,10,false,false,2,2",
    ])
    t = parse_binance_csv(p)
    assert len(t) == 1
    assert t[0].side == "LONG"
    assert t[0].entry_price == 100 and t[0].exit_price == 110
    assert t[0].realized_pnl == 10
    assert abs(t[0].net_pnl - (10 - 0.105)) < 1e-6      # RP − fees


def test_short_round_trip():
    # SELL เปิด แล้ว BUY ปิด → SHORT
    p = _csv([
        "1,2026-08-08 10:00:00,ETHUSDT,SELL,100,1,100,0.05USDT,0,false,false,1,1",
        "1,2026-08-08 11:00:00,ETHUSDT,BUY,95,1,95,0.0475USDT,5,true,false,2,2",
    ])
    t = parse_binance_csv(p)
    assert len(t) == 1 and t[0].side == "SHORT"
    assert t[0].realized_pnl == 5


def test_multiple_fills_aggregate_into_one_position():
    # 2 BUY (เฉลี่ยราคา) แล้ว SELL ปิดทั้งก้อน
    p = _csv([
        "1,2026-08-08 10:00:00,BTCUSDT,BUY,100,1,100,0.05USDT,0,true,false,1,1",
        "1,2026-08-08 10:05:00,BTCUSDT,BUY,102,1,102,0.05USDT,0,true,false,2,1",
        "1,2026-08-08 11:00:00,BTCUSDT,SELL,110,2,220,0.11USDT,16,false,false,3,2",
    ])
    t = parse_binance_csv(p)
    assert len(t) == 1
    assert t[0].qty == 2
    assert t[0].entry_price == 101                       # เฉลี่ย (100+102)/2
    assert t[0].realized_pnl == 16


def test_two_separate_round_trips_same_symbol():
    p = _csv([
        "1,2026-08-08 10:00:00,TIAUSDT,SELL,0.32,100,32,0.01USDT,0,false,false,1,1",
        "1,2026-08-08 11:00:00,TIAUSDT,BUY,0.31,100,31,0.01USDT,1,true,false,2,2",
        "1,2026-08-08 12:00:00,TIAUSDT,SELL,0.30,100,30,0.01USDT,0,false,false,3,3",
        "1,2026-08-08 13:00:00,TIAUSDT,BUY,0.31,100,31,0.01USDT,-1,true,false,4,4",
    ])
    t = parse_binance_csv(p)
    assert len(t) == 2
    assert t[0].realized_pnl == 1 and t[1].realized_pnl == -1


def test_fee_with_space_and_usdt_suffix_parses():
    p = _csv([
        "1,2026-08-08 10:00:00,BTCUSDT,BUY,100,1,100,0.09792840 USDT,0,true,false,1,1",
        "1,2026-08-08 11:00:00,BTCUSDT,SELL,100,1,100,0.09761400 USDT,0,false,false,2,2",
    ])
    t = parse_binance_csv(p)
    assert len(t) == 1
    assert abs(t[0].fees - (0.0979284 + 0.097614)) < 1e-6


def test_match_finds_nearest_within_window_else_none():
    rt = RealTrade("BTCUSDT", "LONG", 1_000_000_000_000, 1_000_003_600_000,
                   100, 110, 1, 10, 0.1)
    paper = [
        {"symbol": "BTCUSDT", "opened_at": 1_000_000_600_000, "filled_entry": 100},  # +10 นาที
        {"symbol": "BTCUSDT", "opened_at": 1_000_000_000_000 + 10_000_000, "filled_entry": 99},
        {"symbol": "ETHUSDT", "opened_at": 1_000_000_000_000, "filled_entry": 50},   # คนละเหรียญ
    ]
    m = match(rt, paper, window_ms=3_600_000)
    assert m is not None and m["opened_at"] == 1_000_000_600_000   # อันใกล้สุด
    # นอกกรอบ → None
    far = RealTrade("BTCUSDT", "LONG", 5_000_000_000_000, 5_000_000_000_000, 1, 1, 1, 0, 0)
    assert match(far, paper, window_ms=3_600_000) is None

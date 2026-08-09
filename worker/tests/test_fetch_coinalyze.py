"""ตัว parse ของ Coinalyze OI fetcher — เทสต์ได้โดยไม่ต้องต่อเน็ต/ใช้ key

รูปแบบ response: [{"symbol","history":[{"t":วินาที,"o","h","l","c"}]}]
t เป็นวินาที → ต้องแปลงเป็น ms ให้ตรงกับ CSV อื่นในโปรเจกต์
"""
import csv
import os
import tempfile

import pytest

from scripts.fetch_coinalyze_oi import (_parse_history, coinalyze_symbol,
                                        write_csv)


def test_symbol_format_is_binance_perp():
    assert coinalyze_symbol("BTCUSDT") == "BTCUSDT_PERP.A"
    assert coinalyze_symbol("ethusdt") == "ETHUSDT_PERP.A"


def test_parses_history_and_converts_seconds_to_ms():
    payload = [{"symbol": "BTCUSDT_PERP.A", "history": [
        {"t": 2000, "o": 11, "h": 12, "l": 10, "c": 11.5},
        {"t": 1000, "o": 9, "h": 10, "l": 8, "c": 9.5},
    ]}]
    rows = _parse_history(payload)
    assert [r[0] for r in rows] == [1_000_000, 2_000_000]   # วินาที→ms, เรียงเก่า→ใหม่
    assert rows[0] == [1_000_000, 9.0, 10.0, 8.0, 9.5]


def test_error_body_raises():
    with pytest.raises(SystemExit):
        _parse_history({"message": "invalid api_key"})


def test_empty_history_is_empty():
    assert _parse_history([{"symbol": "X", "history": []}]) == []
    assert _parse_history([]) == []


def test_malformed_points_skipped():
    payload = [{"symbol": "X", "history": [
        {"t": 1000, "o": 9, "h": 10, "l": 8, "c": 9.5},
        {"t": 2000, "o": "bad"},                 # เสีย → ข้าม
        {"nope": 1},                             # ไม่มี t → ข้าม
    ]}]
    rows = _parse_history(payload)
    assert len(rows) == 1 and rows[0][0] == 1_000_000


def test_write_csv_roundtrip():
    rows = [[1_000_000, 9.0, 10.0, 8.0, 9.5]]
    path = os.path.join(tempfile.mkdtemp(), "BTCUSDT_1d_oi.csv")
    write_csv(rows, path)
    with open(path) as f:
        got = list(csv.reader(f))
    assert got[0] == ["open_time", "oi_open", "oi_high", "oi_low", "oi_close"]
    assert got[1][0] == "1000000"

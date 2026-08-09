"""market_snapshots — เก็บ OI/funding สดไว้สะสมสำหรับวิจัยภายหลัง

infrastructure ล้วน (ไม่ใช่ edge) เทสต์ว่าเขียนถูก, idempotent ต่อแท่ง,
และคีย์ที่ขาดจาก fetcher ไม่ทำให้พัง
"""
import os
import tempfile

from worker.app.journal import Journal


def _journal():
    return Journal(os.path.join(tempfile.mkdtemp(), "j.db"))


def _snap(oi=100.0, oiv=6_000_000.0, funding=0.0001, mark=64000.0):
    return {"open_interest": oi, "open_interest_value": oiv,
            "funding_rate": funding, "mark_price": mark}


def test_snapshot_is_written_and_read_back():
    j = _journal()
    j.record_snapshot("BTCUSDT", 1_700_000_000_000, 63950.0, _snap())
    row = j.conn.execute("SELECT * FROM market_snapshots").fetchone()
    assert row["symbol"] == "BTCUSDT"
    assert row["open_interest"] == 100.0
    assert row["open_interest_value"] == 6_000_000.0
    assert row["funding_rate"] == 0.0001
    assert row["price"] == 63950.0
    j.close()


def test_snapshot_is_idempotent_per_candle():
    """replay แท่งเดิม (เช่น reconnect/backfill) ต้องไม่บันทึกซ้ำ"""
    j = _journal()
    ts = 1_700_000_000_000
    j.record_snapshot("BTCUSDT", ts, 63950.0, _snap(oi=100.0))
    j.record_snapshot("BTCUSDT", ts, 63951.0, _snap(oi=999.0))   # ค่าเปลี่ยน แต่ ts เดิม
    rows = j.conn.execute("SELECT * FROM market_snapshots WHERE symbol='BTCUSDT'").fetchall()
    assert len(rows) == 1
    assert rows[0]["open_interest"] == 100.0                     # เก็บตัวแรก ไม่ทับ
    j.close()


def test_different_symbols_and_candles_coexist():
    j = _journal()
    j.record_snapshot("BTCUSDT", 1000, 1.0, _snap())
    j.record_snapshot("ETHUSDT", 1000, 2.0, _snap())            # เหรียญต่าง ts เดียวกัน
    j.record_snapshot("BTCUSDT", 2000, 3.0, _snap())            # เหรียญเดียว ts ต่าง
    assert j.conn.execute("SELECT COUNT(*) c FROM market_snapshots").fetchone()["c"] == 3
    j.close()


def test_missing_fields_do_not_crash():
    """fetcher อาจคืน dict ไม่ครบคีย์ — ต้องเขียนเป็น NULL ไม่ใช่ระเบิด"""
    j = _journal()
    j.record_snapshot("BTCUSDT", 1000, None, {"funding_rate": 0.0002})
    row = j.conn.execute("SELECT * FROM market_snapshots").fetchone()
    assert row["funding_rate"] == 0.0002
    assert row["open_interest"] is None
    assert row["price"] is None
    j.close()

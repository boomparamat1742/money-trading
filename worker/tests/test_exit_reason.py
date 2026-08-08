"""สาเหตุการปิดสถานะ — ต้องแยกให้ออกว่า "โดน SL" แบบไหน

status = hit_sl บอกแค่ว่าเกิดอะไร ไม่ได้บอกว่าควรแก้อะไร เทสต์ชุดนี้ล็อกว่า
แต่ละสถานการณ์ถูกจัดกลุ่มถูกต้อง เพราะสถิติที่จัดกลุ่มผิดจะพาไปแก้ผิดจุด
"""
import pytest

from worker.app.config import Fees
from worker.app.models import (Candle, Direction, RiskDecision, RiskStatus,
                               TradeStatus)
from worker.app.notifier import format_close
from worker.app.paper_trading import PaperBroker, attach_exit_market, classify_exit

FREE = Fees(taker_fee_pct=0.0, slippage_pct=0.0)


def _candle(o, h, l, c, t=0):
    return Candle("binance", "BTCUSDT", "15m", t, o, h, l, c, volume=10.0)


def _decision(entry=100.0, sl=98.0, tp=106.0):
    return RiskDecision(status=RiskStatus.APPROVED, entry_price=entry, stop_loss=sl,
                        take_profit=tp, position_size=1.0, risk_amount=2.0, risk_pct=1.0)


def _open(broker=None, side=Direction.LONG, **kw):
    broker = broker or PaperBroker(FREE)
    return broker, broker.open(_decision(**kw), side, "BTCUSDT", None, _candle(100, 100, 100, 100))


def test_initial_stop_is_kept_separately_from_stop_loss():
    """trailing เขียนทับ stop_loss — ถ้าไม่เก็บของเดิมไว้ก็แยกสองกรณีไม่ออก"""
    _, t = _open()
    assert t.initial_stop == 98.0
    t.stop_loss = 101.0
    assert t.initial_stop == 98.0


def test_sl_without_ever_moving_our_way_is_never_worked():
    broker, t = _open()
    broker.update(t, _candle(100, 100.1, 97.0, 97.5, t=1))
    assert t.status == TradeStatus.HIT_SL
    assert t.exit_reason == "sl_initial"
    assert t.exit_context["pattern"] == "never_worked"
    assert t.exit_context["fast_stop"] is True      # โดนภายใน 2 แท่ง


def test_sl_after_being_well_in_profit_is_gave_back():
    """เคยกำไรเกิน 1R แล้วคืนหมด — คนละปัญหากับ never_worked"""
    broker, t = _open()                              # R = 2.0 (100 → 98)
    broker.update(t, _candle(100, 103.0, 100, 102, t=1))   # MFE = +3 = 1.5R
    broker.update(t, _candle(102, 102, 97.0, 97.5, t=2))   # กลับมาโดน SL เดิม
    assert t.status == TradeStatus.HIT_SL
    assert t.exit_reason == "sl_initial"
    assert t.exit_context["pattern"] == "gave_back"
    assert t.exit_context["mfe_r"] == pytest.approx(1.5)
    assert t.exit_context["fast_stop"] is False


def test_partial_move_then_sl_is_stalled():
    broker, t = _open()
    broker.update(t, _candle(100, 101.2, 100, 101, t=1))   # MFE = +1.2 = 0.6R
    broker.update(t, _candle(101, 101, 97.0, 97.5, t=2))
    assert t.exit_context["pattern"] == "stalled"


def test_trailing_stop_exit_is_not_counted_as_a_failed_thesis():
    """SL ที่ถูกเลื่อนแล้วคือระบบล็อกกำไรสำเร็จ ไม่ใช่สมมติฐานผิด
    ถ้ารวมเข้ากับ sl_initial สถิติ 'แพ้' จะบวมเกินจริง"""
    broker = PaperBroker(FREE, trail_r_activate=1.0, trail_r_dist=0.5)
    _, t = _open(broker, tp=200.0)                   # TP ไกล ไม่ให้ไปโดนก่อน
    broker.update(t, _candle(100, 104.0, 100, 103, t=1))   # +2R → เลื่อน SL เป็น 103
    assert t.stop_loss > t.initial_stop
    broker.update(t, _candle(103, 103, 102.0, 102.5, t=2))
    assert t.status == TradeStatus.HIT_SL
    assert t.exit_reason == "sl_trailing"
    assert t.exit_context["pattern"] == "trail_locked"
    assert t.exit_context["stop_moved"] is True
    assert t.pnl_amount > 0                          # ปิดแบบมีกำไร


def test_short_side_trailing_moves_the_other_way():
    """SHORT เลื่อน stop ลง — ถ้าเทียบทิศผิดจะอ่านเป็น sl_initial"""
    broker = PaperBroker(FREE, trail_r_activate=1.0, trail_r_dist=0.5)
    _, t = _open(broker, side=Direction.SHORT, entry=100.0, sl=102.0, tp=1.0)
    broker.update(t, _candle(100, 100, 96.0, 97, t=1))     # กำไร 2R → SL ลงมา 97
    assert t.stop_loss < t.initial_stop
    broker.update(t, _candle(97, 98.0, 97, 97.5, t=2))
    assert t.exit_reason == "sl_trailing"


def test_tp_and_expiry_have_their_own_reasons():
    broker, t = _open()
    broker.update(t, _candle(100, 107.0, 100, 106.5, t=1))
    assert (t.exit_reason, t.exit_context["pattern"]) == ("tp", "target_hit")

    broker = PaperBroker(FREE, max_holding_bars=2)
    _, t2 = _open(broker)
    broker.update(t2, _candle(100, 100.5, 99.5, 100, t=1))
    broker.update(t2, _candle(100, 100.5, 99.5, 100, t=2))
    assert (t2.exit_reason, t2.exit_context["pattern"]) == ("expired", "timeout")


def test_classify_exit_is_pure_and_reusable():
    """ใช้ได้กับไม้ที่โหลดกลับมาจาก journal ด้วย ไม่ผูกกับ broker"""
    _, t = _open()
    t.status = TradeStatus.HIT_SL
    t.max_favorable_excursion = 4.0                  # 2R
    assert classify_exit(t) == ("sl_initial", "gave_back")


def test_alert_names_the_reason_not_just_the_status():
    broker, t = _open()
    broker.update(t, _candle(100, 100.1, 97.0, 97.5, t=1))
    msg = format_close(t)
    assert "โดน SL เดิม" in msg
    assert "จังหวะเข้า" in msg                        # อธิบายว่าควรไปดูตรงไหน
    assert "R" in msg and "PnL" in msg
    assert "hit_sl" not in msg                       # ไม่โยนโค้ดดิบใส่หน้าผู้ใช้


def test_alert_marks_trailing_exit_differently():
    broker = PaperBroker(FREE, trail_r_activate=1.0, trail_r_dist=0.5)
    _, t = _open(broker, tp=200.0)
    broker.update(t, _candle(100, 104.0, 100, 103, t=1))
    broker.update(t, _candle(103, 103, 102.0, 102.5, t=2))
    msg = format_close(t)
    assert "trailing stop" in msg
    assert "🟡" in msg and "🔴" not in msg


def test_exit_market_context_is_attached_when_available():
    from worker.app.indicators import IndicatorEngine

    eng = IndicatorEngine()
    for i in range(80):
        eng.update(Candle("binance", "BTCUSDT", "15m", i * 900_000,
                          100 + i % 5, 102 + i % 5, 98 + i % 5, 100 + i % 5, 10.0))
    snap = eng.update(Candle("binance", "BTCUSDT", "15m", 80 * 900_000,
                             100, 102, 98, 100, 10.0))
    broker, t = _open()
    broker.update(t, _candle(100, 100.1, 97.0, 97.5, t=1))
    attach_exit_market(t, snap)
    assert "rsi" in t.exit_context["market"]
    assert "adx" in t.exit_context["market"]


def test_attach_exit_market_ignores_open_trades():
    """ไม้ที่ยังไม่ปิดไม่มี exit_context — ต้องไม่สร้างขึ้นมามั่ว"""
    _, t = _open()
    attach_exit_market(t, object())
    assert t.exit_context == {}


# ---------- เก็บลง journal แล้วดึงกลับมาวิเคราะห์ ----------

def _journal():
    import os
    import tempfile

    from worker.app.journal import Journal
    return Journal(os.path.join(tempfile.mkdtemp(), "j.db"))


def test_exit_reason_survives_a_round_trip_to_the_database():
    j = _journal()
    broker, t = _open()
    j.open_trade(t)
    broker.update(t, _candle(100, 100.1, 97.0, 97.5, t=1))
    j.close_trade(t)

    row = j.recent_trades(1)[0]
    assert row["exit_reason"] == "sl_initial"
    assert row["initial_stop"] == 98.0
    import json
    assert json.loads(row["exit_context"])["pattern"] == "never_worked"
    j.close()


def test_exit_reasons_groups_by_cause_for_research():
    """คำถามที่ตารางนี้ต้องตอบได้: ระบบตายเพราะอะไรบ่อยที่สุด"""
    j = _journal()
    for i in range(3):                       # 3 ไม้ never_worked
        broker, t = _open()
        j.open_trade(t)
        broker.update(t, _candle(100, 100.1, 97.0, 97.5, t=i))
        j.close_trade(t)
    broker, t = _open()                      # 1 ไม้ถึง TP
    j.open_trade(t)
    broker.update(t, _candle(100, 107.0, 100, 106.5, t=9))
    j.close_trade(t)

    by_reason = {(r["exit_reason"], r["pattern"]): r for r in j.exit_reasons()}
    assert by_reason[("sl_initial", "never_worked")]["n"] == 3
    assert by_reason[("sl_initial", "never_worked")]["fast_stops"] == 3
    assert by_reason[("tp", "target_hit")]["n"] == 1
    assert j.exit_reasons()[0]["n"] == 3     # เรียงตามจำนวนมากไปน้อย
    j.close()


def test_migration_adds_columns_to_a_database_made_before_the_change():
    """DB ที่รันมาก่อนหน้านี้ต้องใช้ต่อได้ ไม่ใช่พังตอน INSERT"""
    import os
    import sqlite3
    import tempfile

    from worker.app.journal import ADDED_TRADE_COLUMNS, Journal

    path = os.path.join(tempfile.mkdtemp(), "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""CREATE TABLE trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER, symbol TEXT,
        side TEXT, status TEXT, requested_entry REAL, filled_entry REAL,
        stop_loss REAL, take_profit REAL, position_size REAL, risk_amount REAL,
        risk_pct REAL, entry_fee REAL, exit_fee REAL, slippage REAL,
        exit_price REAL, pnl_amount REAL, pnl_pct REAL, actual_rr REAL,
        mfe REAL, mae REAL, bars_held INTEGER, init_risk REAL, extreme REAL,
        opened_at INTEGER, closed_at INTEGER, created_at TEXT, updated_at TEXT);""")
    conn.commit()
    conn.close()

    j = Journal(path)                         # ต้อง ALTER ให้เอง
    have = {r["name"] for r in j.conn.execute("PRAGMA table_info(trades)")}
    assert set(ADDED_TRADE_COLUMNS) <= have

    broker, t = _open()                       # แล้วต้องเขียนได้จริง
    j.open_trade(t)
    broker.update(t, _candle(100, 100.1, 97.0, 97.5, t=1))
    j.close_trade(t)
    assert j.recent_trades(1)[0]["exit_reason"] == "sl_initial"
    j.close()

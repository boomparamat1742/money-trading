"""สรุปเหตุการณ์รอบวัน — aggregation + query ตามช่วงเวลา + ข้อความ"""
import os
import tempfile

from worker.app.config import Fees
from worker.app.journal import Journal, summarize_day
from worker.app.models import Candle, Direction, RiskDecision, RiskStatus, TradeStatus
from worker.app.notifier import format_daily_summary
from worker.app.paper_trading import PaperBroker

FREE = Fees(taker_fee_pct=0.0, slippage_pct=0.0)
DAY = 86_400_000


def _rows(*specs):
    # specs: (symbol, pnl, rr, exit_reason)
    return [{"symbol": s, "pnl_amount": p, "actual_rr": rr, "exit_reason": er}
            for s, p, rr, er in specs]


def test_summarize_counts_wins_losses_and_pnl():
    s = summarize_day(opened=5, rows=_rows(
        ("BTCUSDT", 2.0, 1.9, "tp"),
        ("ETHUSDT", -1.0, -1.0, "sl_initial"),
        ("BTCUSDT", 0.5, 0.5, "sl_trailing"),
    ))
    assert s["opened"] == 5 and s["closed"] == 3
    assert s["wins"] == 2 and s["losses"] == 1
    assert s["net_pnl"] == 1.5
    assert s["by_exit"] == {"tp": 1, "sl_initial": 1, "sl_trailing": 1}
    assert s["by_symbol"]["BTCUSDT"]["n"] == 2


def test_best_and_worst_by_rr():
    s = summarize_day(2, _rows(("A", 1, 2.5, "tp"), ("B", -1, -1.1, "sl_initial")))
    assert s["best"]["symbol"] == "A" and s["best"]["rr"] == 2.5
    assert s["worst"]["symbol"] == "B" and s["worst"]["rr"] == -1.1


def test_empty_day_is_safe():
    s = summarize_day(0, [])
    assert s["closed"] == 0 and s["win_rate"] == 0.0 and s["best"] is None
    msg = format_daily_summary(s, 0, DAY)
    assert "ไม่มีไม้ปิด" in msg


def test_message_has_headline_and_disclaimer():
    s = summarize_day(3, _rows(("BTCUSDT", 2.0, 1.9, "tp"), ("ETHUSDT", -1.0, -1.0, "sl_initial")))
    msg = format_daily_summary(s, 0, DAY)
    assert "สรุปเหตุการณ์ทางเทคนิค" in msg
    assert "ชนะ/แพ้: 1/1" in msg
    assert "ยังไม่พิสูจน์ว่ามี edge" in msg     # disclaimer ติดเสมอ


def _journal():
    return Journal(os.path.join(tempfile.mkdtemp(), "j.db"))


def _closed_trade(j, symbol, opened_at, win):
    b = PaperBroker(FREE)
    d = RiskDecision(RiskStatus.APPROVED, 100.0, 98.0, 106.0, 1.0, 2.0, 1.0)
    t = b.open(d, Direction.LONG, symbol, None,
               Candle("binance", symbol, "15m", opened_at, 100, 100, 100, 100, 10))
    j.open_trade(t)
    bar = Candle("binance", symbol, "15m", opened_at + 900_000,
                 100, 107, 100, 106.5, 10) if win else \
          Candle("binance", symbol, "15m", opened_at + 900_000, 100, 100.1, 97, 97.5, 10)
    b.update(t, bar)
    j.close_trade(t)
    return t


def test_daily_summary_only_counts_trades_in_window():
    j = _journal()
    base = 1_700_000_000_000
    _closed_trade(j, "BTCUSDT", base, win=True)             # ในช่วง
    _closed_trade(j, "ETHUSDT", base + 3_600_000, win=False)  # ในช่วง
    _closed_trade(j, "BNBUSDT", base - 2 * DAY, win=True)   # ก่อนช่วง → ต้องไม่นับ
    s = j.daily_summary(base - DAY // 2, base + DAY)
    assert s["closed"] == 2                                 # เฉพาะ 2 ไม้ในช่วง
    assert s["wins"] == 1 and s["losses"] == 1
    j.close()

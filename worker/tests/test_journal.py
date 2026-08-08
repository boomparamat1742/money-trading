"""Journal persistence: idempotent signals, trade lifecycle, restart recovery."""
import os
import tempfile

from worker.app.config import Fees
from worker.app.journal import Journal
from worker.app.models import (Candle, Direction, RiskDecision, RiskStatus,
                               Signal, TradeStatus)
from worker.app.paper_trading import PaperBroker


def _journal():
    path = os.path.join(tempfile.mkdtemp(), "j.db")
    return Journal(path)


def _signal(candle_time=1_700_000_000_000):
    return Signal(
        exchange="binance", symbol="BTCUSDT", timeframe="15m",
        candle_open_time=candle_time, strategy_name="trend_following",
        strategy_version="1.1.0", direction=Direction.LONG, signal_score=78,
        score_breakdown={"trend": 20}, market_regime={"regime": "uptrend"},
        entry_price=65000, stop_loss=63500, take_profit=68000, expected_rr=2.0,
        risk_status="approved", rejection_reason=None, indicators={"rsi": 55},
        trigger_reasons=["htf_aligned"], status="approved",
        position_size=0.033, risk_amount=50.0, risk_pct=0.5)


def _decision():
    return RiskDecision(RiskStatus.APPROVED, entry_price=100.0, stop_loss=97.0,
                        take_profit=106.0, position_size=10.0, risk_amount=30.0,
                        risk_pct=1.0, expected_rr=2.0)


def _candle(i, o=100, h=101, l=99, c=100):
    return Candle("binance", "BTCUSDT", "15m", 1_700_000_000_000 + i * 900_000, o, h, l, c, 100.0)


def test_signal_insert_is_idempotent_per_candle():
    j = _journal()
    a = j.record_signal(_signal())
    b = j.record_signal(_signal())          # same candle replayed
    assert a is not None and a == b          # same row, no duplicate
    assert j.stats()["signals"] == 1
    c = j.record_signal(_signal(candle_time=1_700_000_900_000))
    assert c != a and j.stats()["signals"] == 2


def test_trade_lifecycle_recorded():
    j = _journal()
    sid = j.record_signal(_signal())
    broker = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0))
    t = broker.open(_decision(), Direction.LONG, "BTCUSDT", None, _candle(0))
    j.open_trade(t, sid)
    assert t.db_id is not None
    assert j.stats()["trades_open"] == 1

    broker.update(t, _candle(1, 100, 107, 99, 106))   # hits TP
    j.close_trade(t)
    s = j.stats()
    assert t.status == TradeStatus.HIT_TP
    assert s["trades_open"] == 0 and s["trades_closed"] == 1
    assert s["wins"] == 1 and s["net_pnl"] > 0


def test_open_trades_survive_restart_with_trailing_state():
    j = _journal()
    broker = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0),
                         trail_r_activate=1.0, trail_r_dist=1.0)
    t = broker.open(_decision(), Direction.LONG, "BTCUSDT", None, _candle(0))
    j.open_trade(t)
    broker.update(t, _candle(1, 100, 104, 99, 103))   # +1.3R → stop trails up
    j.update_trade(t)
    trailed_stop, bars, extreme = t.stop_loss, t.bars_held, t.extreme
    assert trailed_stop > 97.0

    # simulate restart: new Journal on the same file, reload
    j2 = Journal(j.path)
    restored = j2.load_open_trades("BTCUSDT")
    assert len(restored) == 1
    r = restored[0]
    assert r.db_id == t.db_id
    assert r.stop_loss == trailed_stop      # trailing stop survived
    assert r.bars_held == bars              # holding period survived
    assert r.extreme == extreme             # trailing reference survived
    assert r.status == TradeStatus.OPEN

    # and it keeps working after restore
    broker.update(r, _candle(2, 103, 103, 100, 100))
    assert r.bars_held == bars + 1


def test_stats_empty_journal_is_safe():
    s = _journal().stats()
    assert s["trades_closed"] == 0 and s["expectancy"] == 0.0

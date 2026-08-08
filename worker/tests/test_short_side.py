"""SHORT-side correctness across the whole chain: risk sizing, paper fills,
PnL sign, and the leverage/margin suggestion. Long-side math is covered in
test_risk.py / test_paper_trading.py — this guards the mirrored path."""
from worker.app.config import Fees, RiskPolicy
from worker.app.models import (Candle, Direction, IndicatorSnapshot,
                               RiskDecision, RiskStatus, TradeStatus)
from worker.app.notifier import suggest_leverage
from worker.app.paper_trading import PaperBroker
from worker.app.risk import PortfolioState, evaluate_risk


def _candle(i, o, h, l, c):
    return Candle("binance", "BTCUSDT", "15m", 1_700_000_000_000 + i * 900_000, o, h, l, c, 100.0)


def test_short_risk_puts_stop_above_entry():
    policy = RiskPolicy(account_equity=10_000, risk_per_trade_pct=1.0)
    snap = IndicatorSnapshot(ready=True, values={"close": 100.0, "atr": 2.0})
    d = evaluate_risk(Direction.SHORT, snap, policy, PortfolioState())
    assert d.status == RiskStatus.APPROVED
    # short: stop ABOVE entry, target BELOW entry
    assert d.take_profit < d.entry_price < d.stop_loss
    assert d.expected_rr >= 1.5
    # same fixed-risk sizing as long: $100 risk / $3 stop distance
    assert abs(d.position_size - (100.0 / 3.0)) < 1e-3


def _short_decision():
    return RiskDecision(RiskStatus.APPROVED, entry_price=100.0, stop_loss=103.0,
                        take_profit=94.0, position_size=10.0, risk_amount=30.0,
                        risk_pct=1.0, expected_rr=2.0)


def test_short_take_profit_is_profitable():
    broker = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0))
    t = broker.open(_short_decision(), Direction.SHORT, "BTCUSDT", None, _candle(0, 100, 100, 100, 100))
    broker.update(t, _candle(1, 100, 101, 93, 94))  # low crosses TP (down)
    assert t.status == TradeStatus.HIT_TP
    assert t.pnl_amount > 0  # price fell → short profits


def test_short_stop_loss_is_loss():
    broker = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0))
    t = broker.open(_short_decision(), Direction.SHORT, "BTCUSDT", None, _candle(0, 100, 100, 100, 100))
    broker.update(t, _candle(1, 100, 104, 99, 103))  # high crosses SL (up)
    assert t.status == TradeStatus.HIT_SL
    assert t.pnl_amount < 0


def test_short_trailing_stop_ratchets_down():
    broker = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0),
                         trail_r_activate=1.0, trail_r_dist=1.0)
    t = broker.open(_short_decision(), Direction.SHORT, "BTCUSDT", None, _candle(0, 100, 100, 100, 100))
    original_stop = t.stop_loss
    broker.update(t, _candle(1, 100, 100, 96, 96))  # +1.3R in favor → trail
    assert t.status == TradeStatus.OPEN
    assert t.stop_loss < original_stop  # short: stop moves DOWN as price falls


def test_leverage_suggestion_symmetric_for_short():
    # same distance % → same suggested leverage regardless of side
    long_lev = suggest_leverage(entry=100.0, stop=97.0)
    short_lev = suggest_leverage(entry=100.0, stop=103.0)
    assert long_lev and short_lev
    assert long_lev["leverage"] == short_lev["leverage"]

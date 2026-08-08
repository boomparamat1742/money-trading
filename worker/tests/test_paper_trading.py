from worker.app.config import Fees
from worker.app.models import Candle, Direction, RiskDecision, RiskStatus, TradeStatus
from worker.app.paper_trading import PaperBroker


def _candle(i, o, h, l, c):
    return Candle("binance", "BTCUSDT", "15m", 1_700_000_000_000 + i * 900_000, o, h, l, c, 100.0)


def _decision():
    return RiskDecision(RiskStatus.APPROVED, entry_price=100.0, stop_loss=97.0,
                        take_profit=106.0, position_size=10.0, risk_amount=30.0,
                        risk_pct=1.0, expected_rr=2.0)


def test_long_take_profit_is_profitable():
    broker = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0))
    t = broker.open(_decision(), Direction.LONG, "BTCUSDT", None, _candle(0, 100, 100, 100, 100))
    broker.update(t, _candle(1, 100, 107, 99, 106))  # high crosses TP
    assert t.status == TradeStatus.HIT_TP
    assert t.pnl_amount > 0


def test_long_stop_loss_is_loss():
    broker = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0))
    t = broker.open(_decision(), Direction.LONG, "BTCUSDT", None, _candle(0, 100, 100, 100, 100))
    broker.update(t, _candle(1, 100, 101, 96, 97))  # low crosses SL
    assert t.status == TradeStatus.HIT_SL
    assert t.pnl_amount < 0


def test_fees_reduce_pnl():
    no_fee = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0))
    with_fee = PaperBroker(Fees(taker_fee_pct=0.1, slippage_pct=0.05))
    t1 = no_fee.open(_decision(), Direction.LONG, "BTCUSDT", None, _candle(0, 100, 100, 100, 100))
    no_fee.update(t1, _candle(1, 100, 107, 99, 106))
    t2 = with_fee.open(_decision(), Direction.LONG, "BTCUSDT", None, _candle(0, 100, 100, 100, 100))
    with_fee.update(t2, _candle(1, 100, 107, 99, 106))
    assert t2.pnl_amount < t1.pnl_amount


def test_sl_assumed_before_tp_when_both_in_one_bar():
    broker = PaperBroker(Fees(taker_fee_pct=0.0, slippage_pct=0.0))
    t = broker.open(_decision(), Direction.LONG, "BTCUSDT", None, _candle(0, 100, 100, 100, 100))
    broker.update(t, _candle(1, 100, 107, 96, 101))  # both SL(97) and TP(106) inside
    assert t.status == TradeStatus.HIT_SL  # conservative

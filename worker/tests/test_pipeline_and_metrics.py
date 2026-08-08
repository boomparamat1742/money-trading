from backtest.metrics import compute_metrics
from backtest.run_backtest import run
from backtest.synthetic import generate
from worker.app.config import Fees, RiskPolicy
from worker.app.models import Direction, TradeStatus


def test_backtest_runs_and_produces_trades():
    candles = generate(bars=2000, seed=7)
    policy = RiskPolicy(signal_score_threshold=55)  # a bit permissive so we get trades
    out = run(candles, policy, Fees())
    assert out.metrics.trade_count > 0
    # every closed trade has a computed pnl
    for t in out.trades:
        if t.status in (TradeStatus.HIT_TP, TradeStatus.HIT_SL, TradeStatus.EXPIRED):
            assert t.pnl_amount is not None


def test_metrics_math():
    # craft trades by hand → known metrics
    from worker.app.models import PaperTrade

    def trade(pnl):
        t = PaperTrade(None, "BTCUSDT", Direction.LONG,
                       TradeStatus.HIT_TP if pnl > 0 else TradeStatus.HIT_SL,
                       100, 97, 106, 10, 30, 1.0)
        t.pnl_amount = pnl
        return t

    trades = [trade(100), trade(-50), trade(100), trade(-50)]
    m = compute_metrics(trades, starting_equity=10_000)
    assert m.trade_count == 4 and m.wins == 2 and m.losses == 2
    assert m.net_profit == 100.0
    assert m.profit_factor == 2.0
    assert m.expectancy == 25.0

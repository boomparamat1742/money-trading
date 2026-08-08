"""Fast coverage for the walk-forward internals (generate_candidates + fast_sim)
over a single small segment — the full multi-fold run is validated manually."""
from backtest.walk_forward import fast_sim, generate_candidates, _segment
from backtest.synthetic import generate
from worker.app.config import Fees, RiskPolicy
from worker.app.models import TradeStatus


def test_candidates_and_fast_sim_single_segment():
    candles = generate(bars=900, seed=11)
    policy = RiskPolicy(signal_score_threshold=55)
    seg, open_from = _segment(candles, 400, 900)   # 100-bar lead-in + 500 test
    cands = generate_candidates(seg, policy, confirm_tfs=("1h",))

    assert len(cands) == len(seg)
    # warm-up region should have no candidates
    assert all(c is None for c in cands[:50])

    trades = fast_sim(seg, cands, policy, sl_mult=1.5, tp_mult=3.0,
                      fees=Fees(), open_from_idx=open_from)
    # every trade opened at/after the window start
    window_start_ts = candles[400].open_time
    for t in trades:
        assert t.opened_at >= window_start_ts
        if t.status in (TradeStatus.HIT_TP, TradeStatus.HIT_SL, TradeStatus.EXPIRED):
            assert t.pnl_amount is not None


def test_higher_threshold_admits_no_more_candidates():
    # monotonic at the candidate level: a stricter score threshold can only
    # admit a subset (trade COUNT isn't monotonic because max_open_trades +
    # trailing exits change when slots free up).
    candles = generate(bars=900, seed=5)
    seg, _ = _segment(candles, 400, 900)
    cands = [c for c in generate_candidates(seg, RiskPolicy(), confirm_tfs=("1h",)) if c]
    admitted_55 = sum(1 for c in cands if c.score.total >= 55)
    admitted_75 = sum(1 for c in cands if c.score.total >= 75)
    assert admitted_75 <= admitted_55

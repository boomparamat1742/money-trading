"""Edge Lab: statistics, the honesty gates, and the registry.

These tests exist because the FIRST version of the evaluator green-lit a strategy
with a 96% drawdown that beat its benchmark by 0.05 Sharpe. The gates below are
what stops that, so they are worth locking down.
"""
import os
import tempfile

from research.lab.core import Hypothesis, Stats, compute_stats
from research.lab.evaluate import (BENCH_MARGIN, MAX_DRAWDOWN, MIN_OOS_OBS,
                                   evaluate, required_sharpe)
from research.lab.registry import Registry


def test_compute_stats_basic():
    s = compute_stats([0.01] * 365)
    assert s.n == 365
    assert s.total_return > 0.3          # compounding 1%/day for a year
    assert s.max_drawdown == 0.0         # never down
    assert s.hit_rate == 1.0


def test_compute_stats_drawdown():
    s = compute_stats([0.5, -0.5, -0.5])   # 1.5 → 0.75 → 0.375
    assert 0.7 < s.max_drawdown < 0.8


def test_required_sharpe_rises_with_trials():
    """Every extra hypothesis tried must raise the bar — this is the data-mining guard."""
    low = required_sharpe(n_obs=1000, trials=1)
    high = required_sharpe(n_obs=1000, trials=50)
    assert high > low


def _series(daily_ret, n, start=1_700_000_000_000, vol=0.01):
    """Deterministic series with real variance (a constant series has zero vol,
    which makes Sharpe undefined and the gates untestable)."""
    import math
    day = 86_400_000
    t = [start + i * day for i in range(n)]
    r = [daily_ret + vol * math.sin(i * 1.7) for i in range(n)]
    return t, r, [1.0] * n


class _Fake(Hypothesis):
    """Deterministic hypothesis: constant daily return, constant benchmark."""
    name = "fake"
    question = "test"

    def __init__(self, ret, bench_ret, n=1500, neutral=False, crash=False):
        self._ret, self._bench, self._n, self.neutral, self._crash = ret, bench_ret, n, neutral, crash

    def param_grid(self):
        return [{"p": 1}]

    def load(self):
        from research.lab.core import DataBundle
        t, _, _ = _series(0, self._n)
        return DataBundle(prices={"X": {ts: 100.0 for ts in t}})

    def run(self, data, params):
        t, r, p = _series(self._ret, self._n)
        if self._crash:
            r = list(r)
            r[self._n // 2] = -0.9          # one catastrophic day → huge drawdown
        return t, r, p

    def benchmark(self, data):
        t, r, _ = _series(self._bench, self._n)
        return t, r


def test_gate_rejects_when_benchmark_margin_too_small():
    """Beating buy&hold by a hair is noise, not edge."""
    h = _Fake(ret=0.0012, bench_ret=0.00115)   # nearly identical
    ev = evaluate(h, trials_before=0, verbose=False)
    assert not ev.passed
    assert "benchmark" in ev.reason


def test_gate_rejects_catastrophic_drawdown():
    """A strategy that nearly wipes the account is unusable regardless of Sharpe."""
    h = _Fake(ret=0.004, bench_ret=0.0, crash=True)
    ev = evaluate(h, trials_before=0, verbose=False)
    assert ev.oos.max_drawdown > MAX_DRAWDOWN
    assert not ev.passed
    assert "drawdown" in ev.reason


def test_gate_flags_insufficient_data_instead_of_failing():
    """Too little OOS data must read as 'cannot judge', not as 'no edge'."""
    h = _Fake(ret=0.01, bench_ret=0.0, n=600)   # 540 train + 180 test → tiny OOS
    ev = evaluate(h, train_days=540, test_days=30, trials_before=0, verbose=False)
    if ev.oos.n < MIN_OOS_OBS:
        assert not ev.passed and "ตัดสินไม่ได้" in ev.reason


def test_clear_winner_passes_all_gates():
    h = _Fake(ret=0.004, bench_ret=0.0)   # clearly better risk-adjusted return
    ev = evaluate(h, trials_before=0, verbose=False)
    assert ev.oos.sharpe > ev.benchmark.sharpe + BENCH_MARGIN
    assert ev.oos.max_drawdown <= MAX_DRAWDOWN
    assert ev.passed


def test_registry_counts_trials_and_records_failures():
    reg = Registry(os.path.join(tempfile.mkdtemp(), "lab.db"))
    assert reg.trials() == 0
    ev = evaluate(_Fake(ret=0.0001, bench_ret=0.0), trials_before=0, verbose=False)
    reg.record(ev)
    assert reg.trials() == 1
    assert reg.summary()["runs"] == 1     # failures are recorded too
    reg.close()


# --- watcher: การรันซ้ำต้องไม่กลายเป็นการหลอกตัวเอง -------------------------
def _ev(name, passed, sharpe=1.0):
    from research.lab.core import Stats
    from research.lab.evaluate import Evaluation
    return Evaluation(hypothesis=name, question="q", neutral=False,
                      oos=Stats(n=1000, sharpe=sharpe, cagr=0.2),
                      benchmark=Stats(), folds=[], param_counts={},
                      trials_before=0, required_sharpe=0.5, passed=passed, reason="")


def test_first_time_pass_is_flagged_as_suspicious_not_celebrated():
    """ผ่านครั้งแรกหลังทดสอบมาหลายครั้ง = สัญญาณต้องสงสัย ไม่ใช่ข่าวดี"""
    from research.lab.watch import build_alert
    prev = {"passed_recent": 0, "runs_recent": 4, "total_runs": 4}
    after = {"passed_recent": 1, "runs_recent": 5, "total_runs": 5}
    text = build_alert([(_ev("h", True), prev, after)])
    assert "ครั้งแรก" in text and "บังเอิญ" in text


def test_sustained_pass_reads_differently_from_one_off():
    from research.lab.watch import build_alert
    prev = {"passed_recent": 3, "runs_recent": 4, "total_runs": 4}
    after = {"passed_recent": 4, "runs_recent": 5, "total_runs": 5}
    text = build_alert([(_ev("h", True), prev, after)])
    assert "ต่อเนื่อง" in text


def test_decaying_edge_is_reported():
    from research.lab.watch import build_alert
    prev = {"passed_recent": 4, "runs_recent": 5, "total_runs": 5}
    after = {"passed_recent": 4, "runs_recent": 5, "total_runs": 6}
    text = build_alert([(_ev("h", False, sharpe=0.1), prev, after)])
    assert "เสื่อม" in text


def test_no_alert_when_nothing_changed():
    """ไม่ผ่านเหมือนเดิม = เงียบ ไม่สแปม"""
    from research.lab.watch import build_alert
    st = {"passed_recent": 0, "runs_recent": 5, "total_runs": 5}
    assert build_alert([(_ev("h", False), st, st)]) == ""


def test_registry_stability_tracks_repeat_runs():
    reg = Registry(os.path.join(tempfile.mkdtemp(), "s.db"))
    for passed in (False, False, True):
        ev = _ev("repeat", passed)
        reg.record(ev)
    st = reg.stability("repeat", window=5)
    assert st["total_runs"] == 3 and st["runs_recent"] == 3
    assert st["passed_recent"] == 1        # ผ่านแค่ 1 จาก 3 — เห็นชัดว่ายังไม่น่าเชื่อ
    reg.close()

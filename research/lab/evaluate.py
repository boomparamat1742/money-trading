"""Edge Lab evaluator — walk-forward + benchmark + multiple-testing guard.

The hard part of edge research is not running a backtest; it is not fooling
yourself. Two guards are built in and cannot be skipped:

1. **Walk-forward.** Parameters are chosen on a train window and scored on the
   NEXT window only. Aggregated out-of-sample returns are the verdict.

2. **Multiple-testing bar.** Test enough ideas and one will look good by luck.
   The best of `k` independent tries has an expected max Sharpe of roughly
   `sqrt(2 ln k) × SE(Sharpe)` under the null of no edge, so the bar rises with
   every hypothesis already recorded in the registry. This is a heuristic guard
   (a real deflated-Sharpe test needs the trials' correlation), deliberately
   conservative — it exists to stop "keep trying until something passes".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .core import DataBundle, Hypothesis, Stats, compute_stats


# --- practical gates, on top of the statistical one ---------------------------
MIN_OOS_OBS = 180        # ต่ำกว่านี้ตัดสินไม่ได้ (ไม่ใช่ "ไม่ผ่าน" — คือ "ข้อมูลไม่พอ")
MAX_DRAWDOWN = 0.60      # เกินนี้ = เอาไปใช้จริงไม่ได้ ต่อให้ Sharpe สวย
BENCH_MARGIN = 0.25      # directional ต้องชนะ benchmark ด้วยระยะห่างที่ไม่ใช่แค่ noise


@dataclass
class FoldResult:
    idx: int
    params: dict
    train_sharpe: float
    test_sharpe: float
    test_n: int


@dataclass
class Evaluation:
    hypothesis: str
    question: str
    neutral: bool
    oos: Stats
    benchmark: Stats
    folds: list[FoldResult]
    param_counts: dict[str, int]
    trials_before: int
    required_sharpe: float
    passed: bool
    reason: str

    @property
    def folds_positive(self) -> int:
        return sum(1 for f in self.folds if f.test_sharpe > 0)


def _slice(times, rets, pos, lo=None, hi=None):
    st, sr, sp = [], [], []
    for i, t in enumerate(times):
        if (lo is None or t >= lo) and (hi is None or t < hi):
            st.append(t); sr.append(rets[i])
            sp.append(pos[i] if pos else 1.0)
    return st, sr, sp


def sharpe_standard_error(sharpe: float, n: int) -> float:
    """SE of an annualized Sharpe estimated from n daily observations."""
    if n < 2:
        return float("inf")
    from .core import ANNUAL
    # SE of the per-period Sharpe ≈ sqrt((1 + s²/2)/n); annualize by sqrt(ANNUAL)
    s_per = sharpe / math.sqrt(ANNUAL)
    return math.sqrt((1 + 0.5 * s_per ** 2) / n) * math.sqrt(ANNUAL)


def required_sharpe(n_obs: int, trials: int, min_floor: float = 0.3) -> float:
    """Bar the OOS Sharpe must clear, given how many hypotheses have been tried."""
    k = max(trials, 1)
    z = math.sqrt(2 * math.log(k + 1))          # expected max of k draws
    se = sharpe_standard_error(0.0, max(n_obs, 2))
    return max(min_floor, z * se)


def evaluate(hyp: Hypothesis, data: Optional[DataBundle] = None,
             train_days: int = 540, test_days: int = 180,
             trials_before: int = 0, verbose: bool = True) -> Evaluation:
    data = data if data is not None else hyp.load()
    dates = data.dates
    if len(dates) < train_days + test_days + 30:
        raise SystemExit(f"ข้อมูลไม่พอ: มี {len(dates)} วัน ต้องการ ≥ {train_days + test_days + 30}")

    grid = hyp.param_grid()
    if not grid:
        raise SystemExit("param_grid ว่าง — ต้องประกาศพารามิเตอร์ล่วงหน้า")

    # pre-compute each parameter set's full series once, then slice per fold
    series: dict[int, tuple] = {}
    for i, p in enumerate(grid):
        series[i] = hyp.run(data, p)

    folds: list[FoldResult] = []
    oos_t: list[int] = []
    oos_r: list[float] = []
    oos_p: list[float] = []
    param_counts: dict[str, int] = {}

    start = 0
    fold_idx = 0
    while start + train_days + test_days <= len(dates):
        tr_lo, tr_hi = dates[start], dates[start + train_days]
        te_lo = tr_hi
        te_hi = dates[min(start + train_days + test_days, len(dates) - 1)]
        fold_idx += 1

        # choose params on TRAIN only
        best_i, best_s = None, -1e9
        for i, p in enumerate(grid):
            t, r, pos = series[i]
            _, rr, pp = _slice(t, r, pos, tr_lo, tr_hi)
            s = compute_stats(rr, positions=pp).sharpe
            if s > best_s:
                best_s, best_i = s, i

        t, r, pos = series[best_i]
        te_t, te_r, te_p = _slice(t, r, pos, te_lo, te_hi)
        te_stats = compute_stats(te_r, te_t, te_p)
        oos_t += te_t; oos_r += te_r; oos_p += te_p
        key = str(grid[best_i])
        param_counts[key] = param_counts.get(key, 0) + 1
        folds.append(FoldResult(fold_idx, grid[best_i], round(best_s, 3),
                                round(te_stats.sharpe, 3), te_stats.n))
        if verbose:
            print(f"  fold {fold_idx:>2}  train Sharpe {best_s:>6.2f}  "
                  f"→ test Sharpe {te_stats.sharpe:>6.2f}  ({te_stats.n}d)  {grid[best_i]}",
                  flush=True)
        start += test_days

    oos = compute_stats(oos_r, oos_t, oos_p)

    # benchmark over the same out-of-sample span
    bt, br = hyp.benchmark(data)
    if bt and oos_t:
        _, brr, _ = _slice(bt, br, None, oos_t[0], oos_t[-1] + 1)
        bench = compute_stats(brr)
    else:
        bench = Stats()

    bar = required_sharpe(oos.n, trials_before)
    n_pos = sum(1 for f in folds if f.test_sharpe > 0)

    # ข้อมูลไม่พอ → ตัดสินไม่ได้ (ต่างจาก "ไม่ผ่าน")
    if oos.n < MIN_OOS_OBS:
        return Evaluation(
            hypothesis=hyp.name, question=hyp.question, neutral=hyp.neutral,
            oos=oos, benchmark=bench, folds=folds, param_counts=param_counts,
            trials_before=trials_before, required_sharpe=round(bar, 3), passed=False,
            reason=(f"⚠️ ตัดสินไม่ได้ — มีข้อมูล OOS แค่ {oos.n} วัน (ต้องการ ≥ {MIN_OOS_OBS}) "
                    "อาจเพราะข้อมูลสั้นเกินไป หรือกลยุทธ์ไม่เข้าสถานะเลยในช่วง test"))

    positive = oos.sharpe > bar and oos.cagr > 0
    # directional ต้องชนะ benchmark อย่างมีระยะห่าง ไม่ใช่แค่มากกว่าเล็กน้อย
    beats_bench = True if hyp.neutral else (oos.sharpe > bench.sharpe + BENCH_MARGIN)
    consistent = bool(folds) and (n_pos / len(folds)) >= 0.5
    survivable = oos.max_drawdown <= MAX_DRAWDOWN
    passed = bool(positive and beats_bench and consistent and survivable)

    if not positive:
        reason = f"OOS Sharpe {oos.sharpe:.2f} ไม่ผ่านเกณฑ์ {bar:.2f} (ปรับตามจำนวนครั้งที่ลอง)"
    elif not beats_bench:
        reason = (f"ไม่ชนะ benchmark อย่างมีนัย — Sharpe {oos.sharpe:.2f} vs {bench.sharpe:.2f} "
                  f"(ต้องห่างอย่างน้อย {BENCH_MARGIN}) ส่วนต่างเท่านี้คือ noise ไม่ใช่ edge")
    elif not survivable:
        reason = (f"❌ drawdown {oos.max_drawdown*100:.0f}% เกินเพดาน {MAX_DRAWDOWN*100:.0f}% — "
                  "ต่อให้ Sharpe ผ่าน ก็ใช้จริงไม่ได้ (พอร์ตแทบหมดก่อนถึงกำไร)")
    elif not consistent:
        reason = f"ไม่สม่ำเสมอ — เป็นบวกแค่ {n_pos}/{len(folds)} fold"
    else:
        reason = (f"ผ่านทุกเกณฑ์: OOS Sharpe {oos.sharpe:.2f} > {bar:.2f}, "
                  f"ชนะ benchmark ≥{BENCH_MARGIN}, DD {oos.max_drawdown*100:.0f}%, สม่ำเสมอ")

    return Evaluation(
        hypothesis=hyp.name, question=hyp.question, neutral=hyp.neutral,
        oos=oos, benchmark=bench, folds=folds, param_counts=param_counts,
        trials_before=trials_before, required_sharpe=round(bar, 3),
        passed=passed, reason=reason,
    )


def format_evaluation(ev: Evaluation, cost_note: str = "") -> str:
    bench_line = ("cash (market-neutral)" if ev.neutral
                  else f"{ev.benchmark.line()}")
    lines = [
        "",
        "═" * 72,
        f"  {ev.hypothesis}",
        f"  {ev.question}",
        "═" * 72,
        f"  OUT-OF-SAMPLE : {ev.oos.line()}",
        f"  Benchmark     : {bench_line}",
        f"  เกณฑ์ Sharpe   : > {ev.required_sharpe}  "
        f"(ปรับจากจำนวนสมมติฐานที่เคยทดสอบ = {ev.trials_before})",
        f"  ความสม่ำเสมอ  : เป็นบวก {ev.folds_positive}/{len(ev.folds)} fold",
        f"  พารามิเตอร์ที่ถูกเลือกบ่อยสุด:",
    ]
    for k, c in sorted(ev.param_counts.items(), key=lambda kv: -kv[1])[:3]:
        lines.append(f"      {c}× {k}")
    if cost_note:
        lines.append(f"  ต้นทุนที่คิดแล้ว: {cost_note}")
    lines += [
        "",
        f"  {'🟢 ผ่าน — มีสัญญาณว่ามี edge' if ev.passed else '🔴 ไม่ผ่าน'}",
        f"  {ev.reason}",
        "",
        "  ⚠️ ผ่านที่นี่ = 'คุ้มศึกษาต่อ' ไม่ใช่ 'เอาไปเทรดได้เลย' —",
        "     ขั้นถัดไปคือทดสอบ execution จริงด้วยเงินเล็กน้อย",
    ]
    return "\n".join(lines)

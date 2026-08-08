"""Walk-forward analysis (design §8.1) — the real edge test.

Why walk-forward beats a single train/test split: it tunes parameters on an
in-sample window, then validates on the NEXT (unseen) window, and rolls forward
across the whole history. Aggregated out-of-sample (OOS) results across many
folds are far harder to over-fit than one lucky split. If OOS expectancy is
positive across most folds → a candidate edge worth pursuing. If not → no edge,
stop before risking money.

Speed: the expensive indicator/regime/strategy/score step is parameter-
INDEPENDENT, so we compute candidates ONCE per window (pipeline.candidate) and
then cheaply replay each risk-parameter combo (evaluate_risk + PaperBroker) —
the exact same components used in production, so results transfer.

Usage:
    python -m backtest.walk_forward data/BTCUSDT_15m.csv
    python -m backtest.walk_forward data/BTCUSDT_15m.csv 2000 500   # train/test bars
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, replace
from typing import Optional

from worker.app.config import Fees, RiskPolicy
from worker.app.data_quality import TIMEFRAME_MS
from worker.app.htf import MultiTimeframeTrend
from worker.app.models import Candle, TradeStatus
from worker.app.paper_trading import PaperBroker
from worker.app.pipeline import Candidate, SignalPipeline
from worker.app.risk import PortfolioState, evaluate_risk

from .metrics import Metrics, compute_metrics
from .synthetic import generate, load_csv

WARMUP = 300           # bars of indicator warm-up before a window may trade
MIN_TRADES = 8         # a train combo must produce at least this many trades to qualify
DAY_MS = 86_400_000

# Parameter grid (kept small — a big grid over-fits the train window itself)
SL_GRID = [1.0, 1.5, 2.0]     # stop = sl * ATR
TP_GRID = [2.0, 3.0]          # target = tp * ATR
THR_GRID = [55, 65]           # min signal score to trade
DEFAULT_COMBO = (1.5, 3.0, 65)  # fixed baseline for comparison
CONFIRM_TFS = ("1h", "4h")      # higher timeframes that must agree (v1.1)
TRAIL_ACTIVATE = 1.0            # start trailing after +1R
TRAIL_DIST = 1.0               # trail 1R behind best price


def _d(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def generate_candidates(candles: list[Candle], policy: RiskPolicy,
                        confirm_tfs: tuple[str, ...] = CONFIRM_TFS) -> list[Optional[Candidate]]:
    """One indicator pass over a slice → per-candle Candidate (or None).
    Parameter-independent, so reused across every combo for this slice."""
    pipeline = SignalPipeline(policy)
    htf = MultiTimeframeTrend(candles[0].timeframe, list(confirm_tfs))
    out: list[Optional[Candidate]] = []
    for c in candles:
        trend = htf.update(c)
        out.append(pipeline.candidate(c, htf_trend=trend))
    return out


def fast_sim(candles: list[Candle], cands: list[Optional[Candidate]],
             policy: RiskPolicy, sl_mult: float, tp_mult: float, fees: Fees,
             open_from_idx: int) -> list:
    """Replay one parameter combo over precomputed candidates. Only opens trades
    at local index >= open_from_idx (the lead-in only warms indicators)."""
    broker = PaperBroker(fees, trail_r_activate=TRAIL_ACTIVATE, trail_r_dist=TRAIL_DIST)
    portfolio = PortfolioState()
    open_trades: list = []
    all_trades: list = []
    day_pnl = 0.0

    for i, c in enumerate(candles):
        if portfolio.roll_day(c.open_time):  # resets daily loss + loss-streak cooldown
            day_pnl = 0.0

        # settle open trades on this bar
        still = []
        for t in open_trades:
            broker.update(t, c)
            if t.status == TradeStatus.OPEN:
                still.append(t)
            else:
                portfolio.open_trades -= 1
                portfolio.open_risk_pct -= t.risk_pct
                pnl = t.pnl_amount or 0.0
                if pnl <= 0:
                    portfolio.consecutive_losses += 1
                    day_pnl += pnl
                else:
                    portfolio.consecutive_losses = 0
                if day_pnl < 0:
                    portfolio.daily_loss_pct = abs(day_pnl) / policy.account_equity * 100
        open_trades = still

        cand = cands[i]
        if cand is None or i < open_from_idx:
            continue
        if cand.score.total < policy.signal_score_threshold:
            continue
        decision = evaluate_risk(cand.strat.direction, cand.snap, policy,
                                 portfolio, sl_mult, tp_mult)
        if decision.status.value != "approved":
            continue
        t = broker.open(decision, cand.strat.direction, c.symbol, None, c)
        open_trades.append(t)
        all_trades.append(t)
        portfolio.open_trades += 1
        portfolio.open_risk_pct += decision.risk_pct or 0.0

    if candles:
        for t in open_trades:
            broker._close(t, candles[-1].close, TradeStatus.EXPIRED, candles[-1])
    return all_trades


def _segment(candles, seg_start, seg_end):
    """Return (slice_with_leadin, open_from_local_idx)."""
    lead = max(0, seg_start - WARMUP)
    return candles[lead:seg_end], seg_start - lead


@dataclass
class Fold:
    idx: int
    test_from: str
    test_to: str
    combo: tuple
    train_expectancy: float
    train_trades: int
    test_expectancy: float
    test_trades: int
    test_net: float
    valid_params: bool


def run_walk_forward(candles: list[Candle], base_policy: RiskPolicy, fees: Fees,
                     train_bars: int, test_bars: int):
    folds: list[Fold] = []
    all_oos_trades: list = []
    baseline_oos_trades: list = []
    combo_counts: dict[tuple, int] = {}

    train_start = WARMUP
    fold_idx = 0
    while True:
        train_end = train_start + train_bars
        test_start = train_end
        test_end = test_start + test_bars
        if test_end > len(candles):
            break
        fold_idx += 1

        # --- optimize on train (candidates computed once) ---
        tr_slice, tr_open = _segment(candles, train_start, train_end)
        tr_cands = generate_candidates(tr_slice, base_policy)
        best = None  # (combo, metrics)
        for sl in SL_GRID:
            for tp in TP_GRID:
                for thr in THR_GRID:
                    pol = replace(base_policy, signal_score_threshold=thr)
                    trades = fast_sim(tr_slice, tr_cands, pol, sl, tp, fees, tr_open)
                    m = compute_metrics(trades, base_policy.account_equity)
                    if m.trade_count >= MIN_TRADES and (best is None or m.expectancy > best[1].expectancy):
                        best = ((sl, tp, thr), m)
        valid = best is not None
        combo, train_m = best if valid else (DEFAULT_COMBO, Metrics())

        # --- validate on the next (unseen) window ---
        te_slice, te_open = _segment(candles, test_start, test_end)
        te_cands = generate_candidates(te_slice, base_policy)
        sl, tp, thr = combo
        pol = replace(base_policy, signal_score_threshold=thr)
        te_trades = fast_sim(te_slice, te_cands, pol, sl, tp, fees, te_open)
        te_m = compute_metrics(te_trades, base_policy.account_equity)
        all_oos_trades.extend(te_trades)
        combo_counts[combo] = combo_counts.get(combo, 0) + 1

        # baseline (fixed default params) on the same test window
        bsl, btp, bthr = DEFAULT_COMBO
        bpol = replace(base_policy, signal_score_threshold=bthr)
        baseline_oos_trades.extend(fast_sim(te_slice, te_cands, bpol, bsl, btp, fees, te_open))

        folds.append(Fold(
            idx=fold_idx, test_from=_d(candles[test_start].open_time),
            test_to=_d(candles[test_end - 1].open_time), combo=combo,
            train_expectancy=train_m.expectancy, train_trades=train_m.trade_count,
            test_expectancy=te_m.expectancy, test_trades=te_m.trade_count,
            test_net=te_m.net_profit, valid_params=valid,
        ))
        print(f"  fold {fold_idx:>2} {folds[-1].test_from}→{folds[-1].test_to}  "
              f"combo{combo}  train_exp {train_m.expectancy:>8.2f}  "
              f"test_exp {te_m.expectancy:>8.2f} ({te_m.trade_count} tr)", flush=True)

        train_start += test_bars  # roll forward (non-overlapping test coverage)

    return folds, all_oos_trades, baseline_oos_trades, combo_counts


def report(folds, oos_trades, baseline_trades, combo_counts, equity):
    oos = compute_metrics(oos_trades, equity)
    base = compute_metrics(baseline_trades, equity)
    pos = sum(1 for f in folds if f.test_expectancy > 0)
    n = len(folds)

    def pf(m):
        return "∞" if m.profit_factor == float("inf") else f"{m.profit_factor}"

    lines = [
        "",
        "═══ Walk-Forward Summary ═══",
        f"Folds                    : {n}   (test-expectancy > 0 in {pos}/{n} = {round(pos / n * 100) if n else 0}%)",
        "",
        "Aggregate OUT-OF-SAMPLE (tuned per fold — the honest number):",
        f"  Trades                 : {oos.trade_count}  (win {oos.wins} / loss {oos.losses}, {oos.win_rate}%)",
        f"  Net profit             : {oos.net_profit}",
        f"  Profit factor          : {pf(oos)}",
        f"  Expectancy / trade     : {oos.expectancy}",
        f"  Max drawdown           : {oos.max_drawdown}%",
        f"  Total fees             : {oos.total_fees}",
        "",
        f"Baseline (fixed {DEFAULT_COMBO}) OOS expectancy : {base.expectancy}  (PF {pf(base)}, {base.trade_count} tr)",
        "",
        "Parameter stability (how often each combo was picked on train):",
    ]
    for combo, c in sorted(combo_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {combo}: {c}×")

    positive = oos.expectancy > 0 and n > 0 and pos / n >= 0.5
    lines += [
        "",
        ("🟢 มีสัญญาณว่ามี edge — OOS expectancy เป็นบวกและสม่ำเสมอพอ "
         "(ยังต้องตรวจ: ข้อมูลยาวขึ้น, หลายสินทรัพย์, ค่าคอมมิชชั่นจริง, robustness)")
        if positive else
        ("🔴 ยังไม่พบ edge — OOS expectancy ไม่เป็นบวก/ไม่สม่ำเสมอ "
         "อย่านำไปเทรดจริง ปรับกลยุทธ์แล้วทดสอบใหม่"),
    ]
    if oos.expectancy <= 0 < base.expectancy or (base.expectancy and oos.expectancy < base.expectancy):
        lines.append("หมายเหตุ: การจูนต่อ fold ไม่ได้ช่วย (อาจ over-fit train) — สัญญาณว่ากลยุทธ์ยังไม่แข็งแรง")
    return "\n".join(lines)


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    train_bars = int(argv[2]) if len(argv) > 2 else 2000
    test_bars = int(argv[3]) if len(argv) > 3 else 500

    if len(argv) > 1 and os.path.exists(argv[1]):
        stem = os.path.splitext(os.path.basename(argv[1]))[0]
        parts = stem.split("_")
        sym, tf = (parts[0].upper(), parts[-1]) if len(parts) >= 2 and parts[-1] in TIMEFRAME_MS else ("BTCUSDT", "15m")
        candles = load_csv(argv[1], symbol=sym, timeframe=tf)
        print(f"Loaded {len(candles)} candles ({sym} {tf}) from {argv[1]}")
    else:
        if len(argv) > 1:
            print(f"ไม่พบไฟล์ {argv[1]} — ใช้ข้อมูลสังเคราะห์แทน")
            print("ดึงข้อมูลจริง: python -m backtest.fetch_binance BTCUSDT 15m 20000\n")
        candles = generate(bars=20000)
        print(f"Generated {len(candles)} synthetic candles (demo)")

    need = WARMUP + train_bars + test_bars
    if len(candles) < need:
        print(f"ข้อมูลน้อยไป: มี {len(candles)} แท่ง ต้องการอย่างน้อย {need} "
              f"(warmup {WARMUP} + train {train_bars} + test {test_bars})")
        print("ดึงเพิ่ม: python -m backtest.fetch_binance BTCUSDT 15m 20000")
        return

    policy = RiskPolicy()
    fees = Fees()
    print(f"\nWalk-forward: train={train_bars} bars, test={test_bars} bars, "
          f"warmup={WARMUP}, grid={len(SL_GRID) * len(TP_GRID) * len(THR_GRID)} combos/fold\n")
    t0 = time.time()
    folds, oos, base, counts = run_walk_forward(candles, policy, fees, train_bars, test_bars)
    print(report(folds, oos, base, counts, policy.account_equity))
    print(f"\n(เสร็จใน {time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main(sys.argv)

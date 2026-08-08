"""Time-Series Momentum (TSMOM) on the daily timeframe — a research-backed edge.

Rule: at each day's close, if the trailing L-day return is positive, hold LONG
next day; otherwise stay FLAT (spot — no shorting). Rebalanced daily; a fee is
charged only when the position flips (turnover), so cost is tiny vs intraday.

Why this is worth testing: time-series momentum is one of the most robust
documented anomalies across assets (Moskowitz-Ooi-Pedersen 2012), and crypto
shows a strong momentum factor (Liu-Tsyvinski-Wu). Its historical benefit is
often DRAWDOWN reduction (sitting out bear markets) as much as higher return —
so we compare risk-adjusted (Sharpe) and max drawdown vs buy & hold, not just
total return.

Usage:
    python -m research.momentum                     # uses data/BTCUSDT_1d.csv (fetch first)
    python -m research.momentum data/ETHUSDT_1d.csv
Fetch daily data:
    python -m backtest.fetch_binance BTCUSDT 1d 2500
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass

from backtest.synthetic import load_csv
from worker.app.config import Fees

FEE = 0.0005          # 0.05% taker per side, charged on position flips
ANNUALIZE = 365       # crypto trades every day


@dataclass
class SeriesStats:
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    exposure: float      # fraction of days holding
    flips: int           # number of position changes (turnover events)


def _stats(day_rets: list[float], times: list[int], positions: list[float]) -> SeriesStats:
    if not day_rets:
        return SeriesStats(0, 0, 0, 0, 0, 0)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in day_rets:
        equity *= (1 + r)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    total = equity - 1
    years = max((times[-1] - times[0]) / (ANNUALIZE * 86_400_000), 1e-9)
    cagr = (equity ** (1 / years)) - 1 if equity > 0 else -1
    mean = sum(day_rets) / len(day_rets)
    var = sum((r - mean) ** 2 for r in day_rets) / max(len(day_rets) - 1, 1)
    sd = math.sqrt(var)
    sharpe = (mean / sd * math.sqrt(ANNUALIZE)) if sd > 0 else 0.0
    exposure = sum(1 for p in positions if p > 0) / len(positions)
    flips = sum(1 for i in range(1, len(positions)) if positions[i] != positions[i - 1])
    return SeriesStats(round(total, 4), round(cagr, 4), round(sharpe, 3),
                       round(max_dd, 4), round(exposure, 3), flips)


def run_series(closes: list[float], times: list[int], lookback: int, fee: float = FEE):
    """Return (times_out, strat_day_rets, buyhold_day_rets, positions).
    Decision uses closes up to t-1 (no look-ahead), applied to day t's return."""
    n = len(closes)
    t_out, strat, bh, pos_series = [], [], [], []
    prev_pos = 0.0
    for t in range(lookback + 1, n):
        past_ret = closes[t - 1] / closes[t - 1 - lookback] - 1
        pos = 1.0 if past_ret > 0 else 0.0
        day_ret = closes[t] / closes[t - 1] - 1
        cost = fee * abs(pos - prev_pos)
        t_out.append(times[t])
        strat.append(pos * day_ret - cost)
        bh.append(day_ret)
        pos_series.append(pos)
        prev_pos = pos
    return t_out, strat, bh, pos_series


def _slice_by_index(seq, start, end):
    return seq[start:end]


def evaluate(closes, times, lookback):
    t_out, strat, bh, pos = run_series(closes, times, lookback)
    return _stats(strat, t_out, pos), _stats(bh, t_out, [1.0] * len(bh))


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    path = argv[1] if len(argv) > 1 else "data/BTCUSDT_1d.csv"
    if not os.path.exists(path):
        print(f"ไม่พบไฟล์ {path}")
        print("ดึงข้อมูลรายวันก่อน:  python -m backtest.fetch_binance BTCUSDT 1d 2500")
        return

    stem = os.path.splitext(os.path.basename(path))[0]
    parts = stem.split("_")
    sym, tf = (parts[0].upper(), parts[-1]) if len(parts) >= 2 else ("BTCUSDT", "1d")
    candles = load_csv(path, symbol=sym, timeframe=tf)
    closes = [c.close for c in candles]
    times = [c.open_time for c in candles]
    print(f"Loaded {len(closes)} {tf} candles for {sym}\n")

    lookbacks = [20, 30, 50, 80, 100, 150, 200]

    # ---- full-sample scan (in-sample — for context only) ----
    print("Lookback scan (full sample — in-sample, ระวัง over-fit):")
    print(f"{'L':>4} | {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>7} {'Expo':>6} {'Flips':>6}")
    for L in lookbacks:
        s, _ = evaluate(closes, times, L)
        print(f"{L:>4} | {s.cagr*100:>7.1f}% {s.sharpe:>7.2f} {s.max_drawdown*100:>6.1f}% "
              f"{s.exposure*100:>5.0f}% {s.flips:>6}")
    _, bh = evaluate(closes, times, lookbacks[0])
    print(f"{'B&H':>4} | {bh.cagr*100:>7.1f}% {bh.sharpe:>7.2f} {bh.max_drawdown*100:>6.1f}% "
          f"{'100':>5}% {'0':>6}")

    # ---- walk-forward: pick L on train, validate on test ----
    split = int(len(closes) * 0.6)
    print(f"\nWalk-forward: train = แท่งที่ 0..{split}, test = {split}..{len(closes)}")
    # score each L on the TRAIN slice only, by Sharpe
    best_L, best_train_sharpe = None, -1e9
    for L in lookbacks:
        tr_t, tr_strat, _, tr_pos = run_series(closes[:split], times[:split], L)
        s = _stats(tr_strat, tr_t, tr_pos)
        if s.sharpe > best_train_sharpe:
            best_train_sharpe, best_L = s.sharpe, L
    # apply chosen L to the TEST slice (with lead-in so lookback is warm)
    lead = max(0, split - best_L - 1)
    te_t, te_strat, te_bh, te_pos = run_series(closes[lead:], times[lead:], best_L)
    # keep only returns whose day index >= split (true OOS)
    oos = [(t, sr, br, p) for t, sr, br, p in zip(te_t, te_strat, te_bh, te_pos) if t >= times[split]]
    if oos:
        ts = [x[0] for x in oos]
        strat_s = _stats([x[1] for x in oos], ts, [x[3] for x in oos])
        bh_s = _stats([x[2] for x in oos], ts, [1.0] * len(oos))
    else:
        strat_s = bh_s = _stats([], [], [])

    print(f"\nเลือก L={best_L} จาก train (Sharpe {best_train_sharpe:.2f})")
    print("\n── ผล OUT-OF-SAMPLE (test) ──")
    print(f"{'':<14}{'TSMOM':>12}{'Buy&Hold':>12}")
    print(f"{'CAGR':<14}{strat_s.cagr*100:>11.1f}%{bh_s.cagr*100:>11.1f}%")
    print(f"{'Sharpe':<14}{strat_s.sharpe:>12.2f}{bh_s.sharpe:>12.2f}")
    print(f"{'Max drawdown':<14}{strat_s.max_drawdown*100:>11.1f}%{bh_s.max_drawdown*100:>11.1f}%")
    print(f"{'Exposure':<14}{strat_s.exposure*100:>11.0f}%{'100':>11}%")
    print(f"{'Total return':<14}{strat_s.total_return*100:>11.1f}%{bh_s.total_return*100:>11.1f}%")

    better_sharpe = strat_s.sharpe > bh_s.sharpe
    better_dd = strat_s.max_drawdown < bh_s.max_drawdown
    print()
    if better_sharpe and better_dd:
        print("🟢 น่าสนใจ — OOS ให้ Sharpe ดีกว่าและ drawdown ต่ำกว่า buy & hold "
              "(momentum ช่วยจริงในเชิง risk-adjusted) ควรตรวจต่อ: หลายสินทรัพย์, ช่วงเวลาอื่น, cost จริง")
    elif better_sharpe or better_dd:
        print("🟡 ก้ำกึ่ง — ดีกว่า buy & hold บางมิติเท่านั้น ต้องทดสอบเพิ่มก่อนสรุป")
    else:
        print("🔴 ยังไม่เห็น edge — ไม่ชนะ buy & hold ทั้ง Sharpe และ drawdown")
    print("หมายเหตุ: spot long/flat เท่านั้น (ไม่ short) · cost 0.05%/flip · ผลอดีตไม่การันตีอนาคต")


if __name__ == "__main__":
    main(sys.argv)

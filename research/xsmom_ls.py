"""Long-Short Cross-Sectional Momentum (market-neutral) — research iteration.

Each weekly rebalance: rank the universe by trailing L-day return, go LONG the
top-K and SHORT the bottom-K (equal weight, gross exposure 2, net ~0). This
isolates the momentum FACTOR from market (BTC) beta — where the documented
crypto momentum alpha actually lives (Liu-Tsyvinski-Wu).

Because it's market-neutral, the benchmark is cash (0): any positive Sharpe
after costs is real, direction-independent edge. We still show long-only XSMOM,
BTC, and equal-weight market for context.

Shorting requires perps. We apply the same trading cost on turnover; perp
funding on the short leg is NOT modeled (a real short-momentum book often also
PAYS funding when shorting popular longs — treat these numbers as optimistic).

Usage:
    python -m research.xsmom_ls        # needs daily data (auto-fetched by xsmom)
"""
from __future__ import annotations

import sys
import time

from research.momentum import _stats
from research.xsmom import UNIVERSE, benchmarks, ensure_data, run_xsmom


def _slice(t, r, lo=None, hi=None):
    st, sr = [], []
    for i in range(len(t)):
        if (lo is None or t[i] >= lo) and (hi is None or t[i] < hi):
            st.append(t[i]); sr.append(r[i])
    return st, sr


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    prices, loaded = {}, []
    for sym in UNIVERSE:
        m = ensure_data(sym)
        if m:
            prices[sym] = m; loaded.append(sym)
    if "BTC" not in prices:
        print("ต้องมี BTC"); return
    dates = sorted(set().union(*[set(s) for s in prices.values()]))
    d0 = time.strftime("%Y-%m-%d", time.gmtime(dates[0] / 1000))
    d1 = time.strftime("%Y-%m-%d", time.gmtime(dates[-1] / 1000))
    print(f"\nUniverse ({len(loaded)}): {', '.join(loaded)}")
    print(f"Daily dates: {len(dates)}  ({d0} → {d1})\n")

    grid = [(L, k) for L in (30, 60, 90) for k in (2, 3, 5)]

    print("Full-sample scan — LONG-SHORT (market-neutral, in-sample):")
    print(f"{'L':>4} {'K':>3} | {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>7}")
    for L, k in grid:
        t, r, _ = run_xsmom(prices, dates, L, k, long_short=True)
        s = _stats(r, t, [1] * len(r))
        print(f"{L:>4} {k:>3} | {s.cagr*100:>7.1f}% {s.sharpe:>7.2f} {s.max_drawdown*100:>6.1f}%")

    # context benchmarks
    (btc_t, btc_r), (mkt_t, mkt_r) = benchmarks(prices, dates)

    # ---- walk-forward on long-short ----
    split_time = dates[int(len(dates) * 0.6)]
    best, best_sharpe = None, -1e9
    for L, k in grid:
        t, r, _ = run_xsmom(prices, dates, L, k, long_short=True)
        tr_t, tr_r = _slice(t, r, hi=split_time)
        s = _stats(tr_r, tr_t, [1] * len(tr_r))
        if s.sharpe > best_sharpe:
            best_sharpe, best = s.sharpe, (L, k)
    L, k = best

    t, r, _ = run_xsmom(prices, dates, L, k, long_short=True)
    ls_te = _slice(t, r, lo=split_time)
    ls = _stats(ls_te[1], ls_te[0], [1] * len(ls_te[1]))
    # long-only same combo OOS, for comparison
    t2, r2, _ = run_xsmom(prices, dates, L, k, long_short=False)
    lo_te = _slice(t2, r2, lo=split_time)
    lo = _stats(lo_te[1], lo_te[0], [1] * len(lo_te[1]))
    # BTC OOS for context
    btc_te_t, btc_te_r = _slice(btc_t, btc_r, lo=split_time)
    btc = _stats(btc_te_r, btc_te_t, [1] * len(btc_te_r))

    print(f"\nWalk-forward: เลือก L={L}, K={k} จาก train (Sharpe {best_sharpe:.2f})")
    print("\n── ผล OUT-OF-SAMPLE (test) ──")
    print(f"{'':<14}{'L/S (neutral)':>15}{'Long-only':>12}{'BTC':>10}")
    print(f"{'CAGR':<14}{ls.cagr*100:>14.1f}%{lo.cagr*100:>11.1f}%{btc.cagr*100:>9.1f}%")
    print(f"{'Sharpe':<14}{ls.sharpe:>15.2f}{lo.sharpe:>12.2f}{btc.sharpe:>10.2f}")
    print(f"{'Max drawdown':<14}{ls.max_drawdown*100:>14.1f}%{lo.max_drawdown*100:>11.1f}%{btc.max_drawdown*100:>9.1f}%")

    print()
    if ls.sharpe > 1.0 and ls.cagr > 0:
        print("🟢 มี edge จริงแบบ market-neutral — L/S Sharpe > 1 หลัง cost (ต่างจากทุกรอบก่อน) "
              "ควรตรวจต่อ: funding ขาช็อต, universe ใหญ่ขึ้น, execution จริง")
    elif ls.sharpe > 0.4 and ls.cagr > 0:
        print("🟡 มีแนวโน้ม — L/S เป็นบวกแบบ market-neutral แต่ Sharpe ปานกลาง คุ้มศึกษาต่อ (funding/execution สำคัญ)")
    elif ls.cagr > 0:
        print("🟡 บวกอ่อนๆ — เป็นบวกแต่ Sharpe ต่ำ กำไรน่าจะถูก funding/cost กินจริง")
    else:
        print("🔴 ไม่มี edge — L/S ติดลบ OOS หลัง cost")
    print("หมายเหตุ: ยังไม่รวม perp funding ฝั่ง short (มักทำให้แย่ลง) · ผลอดีตไม่การันตีอนาคต")


if __name__ == "__main__":
    main(sys.argv)

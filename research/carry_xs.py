"""Cross-Sectional Funding Carry — concentrate on the RICHEST carry.

Improvement over the plain funding carry: instead of holding every positive-
funding coin, each day rank the universe by trailing funding and hold the TOP-N
highest-funding coins (long spot + short perp). This targets where the carry is
richest and sits out coins whose funding has thinned.

Still market-neutral (benchmark = cash = 0). Walk-forward picks (L, N, floor)
on train and validates OOS. Perp funding is the return; execution/borrow and the
risk of funding flipping negative are only partly modeled (flip costs only) —
treat as optimistic.

Usage:
    python -m research.carry_xs
"""
from __future__ import annotations

import sys
import time

from research.funding_carry import (ANNUAL, DAY_MS, ONE_WAY, UNIVERSE,
                                     _stats, always_on, ensure_funding)


def _trailing(m, dates, i, L):
    vals = [m[dates[j]] for j in range(max(0, i - L), i) if dates[j] in m]
    return (sum(vals) / len(vals)) if vals else None


def run_topn(funding, dates, L, N, floor, one_way=ONE_WAY):
    t_out, r_out = [], []
    prev: set[str] = set()
    for i in range(len(dates)):
        d = dates[i]
        scored = []
        for s in funding:
            tm = _trailing(funding[s], dates, i, L)
            if tm is not None and tm > floor and d in funding[s]:
                scored.append((s, tm))
        scored.sort(key=lambda x: -x[1])
        held = set(s for s, _ in scored[:N])

        turnover = 0.0
        if held != prev:
            n_new, n_old = (len(held) or 1), (len(prev) or 1)
            w_new = {s: 1 / n_new for s in held}
            w_old = {s: 1 / n_old for s in prev}
            turnover = sum(abs(w_new.get(s, 0) - w_old.get(s, 0)) for s in held | prev)
            prev = held
        if held:
            ret = sum(funding[s][d] for s in held) / len(held) - one_way * turnover
            t_out.append(d); r_out.append(ret)
    return t_out, r_out


def _slice(t, r, lo=None, hi=None):
    st, sr = [], []
    for i in range(len(t)):
        if (lo is None or t[i] >= lo) and (hi is None or t[i] < hi):
            st.append(t[i]); sr.append(r[i])
    return st, sr


def _fmt(s):
    return (f"{s['ann_return']*100:>7.1f}% {s['sharpe']:>7.2f} {s['ann_vol']*100:>7.1f}% "
            f"{s['max_dd']*100:>6.1f}% {s['pos_days']*100:>5.0f}%")


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    funding, loaded = {}, []
    for sym in UNIVERSE:
        m = ensure_funding(sym)
        if m:
            funding[sym] = m; loaded.append(sym)
    if "BTC" not in funding:
        print("ต้องมี BTC funding"); return
    dates = sorted(set().union(*[set(m) for m in funding.values()]))
    d0 = time.strftime("%Y-%m-%d", time.gmtime(dates[0] / 1000))
    d1 = time.strftime("%Y-%m-%d", time.gmtime(dates[-1] / 1000))
    print(f"\nUniverse ({len(loaded)}): {', '.join(loaded)}")
    print(f"Daily funding days: {len(dates)}  ({d0} → {d1})\n")

    grid = [(L, N, fl) for L in (7, 14, 30) for N in (3, 5) for fl in (0.0, 0.0002, 0.0005)]

    print(f"{'':<26}{'AnnRet':>8}{'Sharpe':>8}{'AnnVol':>8}{'MaxDD':>7}{'Pos%':>6}")
    bt, br = always_on(funding, dates, ["BTC"])
    print(f"{'BTC always-on':<26}{_fmt(_stats(br, bt))}")
    dt, dr = always_on(funding, dates, loaded)
    print(f"{'Diversified always-on':<26}{_fmt(_stats(dr, dt))}")

    # a few top-N combos full-sample for context
    for L, N, fl in [(14, 3, 0.0), (14, 5, 0.0002), (30, 3, 0.0005)]:
        t, r = run_topn(funding, dates, L, N, fl)
        print(f"{f'Top-{N} L={L} floor={fl}':<26}{_fmt(_stats(r, t))}")

    print("\n(market-neutral → benchmark = cash 0; Sharpe > 0 หลัง cost = edge)")

    # ---- walk-forward ----
    split_time = dates[int(len(dates) * 0.6)]
    best, best_sharpe = None, -1e9
    for L, N, fl in grid:
        t, r = run_topn(funding, dates, L, N, fl)
        tr_t, tr_r = _slice(t, r, hi=split_time)
        s = _stats(tr_r, tr_t)
        if s["sharpe"] > best_sharpe:
            best_sharpe, best = s["sharpe"], (L, N, fl)
    L, N, fl = best
    t, r = run_topn(funding, dates, L, N, fl)
    te_t, te_r = _slice(t, r, lo=split_time)
    oos = _stats(te_r, te_t)
    div_te_t, div_te_r = _slice(dt, dr, lo=split_time)
    div_oos = _stats(div_te_r, div_te_t)

    print(f"\nWalk-forward: เลือก L={L}, N={N}, floor={fl} จาก train (Sharpe {best_sharpe:.2f})")
    print("\n── ผล OUT-OF-SAMPLE (test) ──")
    print(f"{'':<18}{'Top-N carry':>13}{'Diversified':>13}")
    print(f"{'Ann return':<18}{oos['ann_return']*100:>12.1f}%{div_oos['ann_return']*100:>12.1f}%")
    print(f"{'Sharpe':<18}{oos['sharpe']:>13.2f}{div_oos['sharpe']:>13.2f}")
    print(f"{'Ann vol':<18}{oos['ann_vol']*100:>12.1f}%{div_oos['ann_vol']*100:>12.1f}%")
    print(f"{'Max drawdown':<18}{oos['max_dd']*100:>12.1f}%{div_oos['max_dd']*100:>12.1f}%")
    print(f"{'Positive days':<18}{oos['pos_days']*100:>12.0f}%{div_oos['pos_days']*100:>12.0f}%")

    print()
    if oos["sharpe"] > 1.0 and oos["ann_return"] > 0:
        print("🟢 มี edge — Top-N carry ให้ Sharpe > 1 หลัง cost แบบ market-neutral OOS "
              "ควรตรวจต่อ: funding ขาลบ, execution จริง, universe ใหญ่ขึ้น")
    elif oos["sharpe"] > 0.5 and oos["ann_return"] > 0:
        print("🟡 มีแนวโน้ม — เป็นบวก Sharpe ปานกลาง คุ้มศึกษาต่อ (execution สำคัญมาก)")
    elif oos["ann_return"] > 0:
        print("🟡 บวกอ่อนๆ — เป็นบวกแต่ Sharpe ต่ำ กำไรน่าจะถูก cost/execution กินจริง")
    else:
        print("🔴 ไม่คุ้ม — OOS ติดลบหลัง cost (funding ยุบในช่วงล่าสุด)")
    print("หมายเหตุ: ช่วง OOS (~2025-26) เป็นช่วง funding ต่ำ · ยังไม่รวม execution/borrow จริง")


if __name__ == "__main__":
    main(sys.argv)
